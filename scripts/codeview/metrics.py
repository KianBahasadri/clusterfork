"""Dependency-free static source metrics for the codeview files pane.

The scanner deliberately does not execute or import repository code.  Python
files get an AST pass when they parse; every other language uses a conservative
lexer plus keyword heuristics.  The returned dictionaries are JSON-ready and
keep estimates explicit in the ``analysis`` field.
"""
from __future__ import annotations

import ast
import math
import re
from collections import Counter


MAX_SYMBOLS = 100

_HASH_COMMENT_LANGS = {"Python", "Shell", "Ruby", "YAML", "TOML", "Vim script"}
_HTML_LANGS = {"HTML", "Markdown"}
_SQL_LANGS = {"SQL"}
_NO_COMMENT_LANGS = {"JSON"}

_GENERIC_LANGS = {
    "C", "C++", "CSS", "Go", "Java", "JavaScript", "Kotlin", "PHP",
    "Rust", "Swift", "TypeScript",
}

_TOKEN_RE = re.compile(
    r"(?P<number>\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?)\b)"
    r"|(?P<ident>[A-Za-z_$][\w$]*)"
    r"|(?P<op>===|!==|>>>|<<=|>>=|=>|==|!=|<=|>=|&&|\|\||\+\+|--|"
    r"\+=|-=|\*=|/=|%=|\*\*|//|<<|>>|\?\?|\?\.|::|->|:=|"
    r"[+\-*/%=<>!&|^~?:])"
)
_TRAILING_WS_RE = re.compile(r"[ \t]+$")

_OPERATOR_WORDS = {
    "and", "as", "async", "await", "break", "case", "catch", "class",
    "const", "continue", "def", "default", "delete", "do", "elif",
    "else", "enum", "except", "export", "extends", "finally", "for",
    "from", "fn", "func", "function", "if", "impl", "import", "in",
    "interface", "is", "let", "match", "new", "not", "of", "or",
    "pub", "raise", "return", "select", "static", "struct", "switch",
    "throw", "trait", "try", "type", "typeof", "use", "var", "void",
    "when", "while", "with", "yield",
}
def empty_metrics(reason: str = "not analyzed") -> dict:
    """Return the stable zero shape used for binary/oversized files."""
    return {
        "analyzed": False,
        "analysis": reason,
        "parse_error": None,
        "total_lines": 0,
        "code_lines": 0,
        "blank_lines": 0,
        "comment_lines": 0,
        "comment_only_lines": 0,
        "inline_comment_lines": 0,
        "comment_blocks": 0,
        "string_lines": 0,
        "characters": 0,
        "unicode_characters": 0,
        "words": 0,
        "tokens": 0,
        "operators": 0,
        "operands": 0,
        "unique_operators": 0,
        "unique_operands": 0,
        "functions": 0,
        "classes": 0,
        "types": 0,
        "imports": 0,
        "exports": 0,
        "declarations": 0,
        "call_sites": 0,
        "parameters": 0,
        "lambdas": 0,
        "docstring_lines": 0,
        "conditionals": 0,
        "loops": 0,
        "exception_handlers": 0,
        "returns": 0,
        "raises": 0,
        "breaks": 0,
        "continues": 0,
        "yields": 0,
        "async_keywords": 0,
        "await_keywords": 0,
        "decision_points": 0,
        "cyclomatic_complexity": 1,
        "max_nesting_depth": 0,
        "max_brace_depth": 0,
        "max_indent_level": 0,
        "max_indent_spaces": 0,
        "max_line_length": 0,
        "avg_line_length": 0,
        "trailing_whitespace_lines": 0,
        "tab_indented_lines": 0,
        "space_indented_lines": 0,
        "final_newline": False,
        "newline_style": "none",
        "todo_count": 0,
        "todo_markers": {},
        "comment_ratio": 0,
        "blank_ratio": 0,
        "function_names": [],
        "class_names": [],
        "import_names": [],
        "export_names": [],
        "halstead": {
            "vocabulary": 0,
            "length": 0,
            "volume": 0,
            "difficulty": 0,
            "effort": 0,
            "estimated_bugs": 0,
        },
        "maintainability_index": None,
        "complexity_rating": "unknown",
    }


def _comment_style(lang: str) -> tuple[tuple[str, ...], tuple[str, str] | None]:
    if lang in _NO_COMMENT_LANGS:
        return (), None
    if lang in _HASH_COMMENT_LANGS:
        return ("#",), None
    if lang in _HTML_LANGS:
        return (), ("<!--", "-->")
    if lang in _SQL_LANGS:
        return ("--",), ("/*", "*/")
    if lang == "PHP":
        return ("//", "#"), ("/*", "*/")
    if lang == "CSS":
        return (), ("/*", "*/")
    if lang in _GENERIC_LANGS:
        return ("//",), ("/*", "*/")
    # Unknown source is still analyzed safely using the most common markers.
    return ("//", "#"), ("/*", "*/")


def _triple_quotes(lang: str) -> bool:
    return lang in {"Python", "Ruby"}


def _lex_lines(text: str, lang: str) -> tuple[list[dict], str]:
    """Strip comments and strings while retaining per-line classifications."""
    line_markers, block_style = _comment_style(lang)
    block_end: str | None = None
    string_delim: str | None = None
    records: list[dict] = []
    comment_parts: list[str] = []

    for raw in text.splitlines():
        code: list[str] = []
        comments: list[str] = []
        has_comment = False
        has_string = False
        i = 0
        while i < len(raw):
            if block_end:
                end = raw.find(block_end, i)
                if end < 0:
                    comments.append(raw[i:])
                    i = len(raw)
                    continue
                comments.append(raw[i:end])
                i = end + len(block_end)
                block_end = None
                continue

            if string_delim:
                has_string = True
                if len(string_delim) == 3:
                    end = raw.find(string_delim, i)
                    if end < 0:
                        i = len(raw)
                        continue
                    i = end + len(string_delim)
                    string_delim = None
                    continue
                escaped = False
                while i < len(raw):
                    char = raw[i]
                    if escaped:
                        escaped = False
                        i += 1
                    elif char == "\\":
                        escaped = True
                        i += 1
                    elif char == string_delim:
                        i += 1
                        string_delim = None
                        break
                    else:
                        i += 1
                continue

            if block_style and raw.startswith(block_style[0], i):
                has_comment = True
                block_end = block_style[1]
                i += len(block_style[0])
                continue
            marker = next((m for m in line_markers if raw.startswith(m, i)), None)
            if marker:
                has_comment = True
                comments.append(raw[i:])
                break

            if raw.startswith(("'''", '"""'), i) and _triple_quotes(lang):
                string_delim = raw[i:i + 3]
                has_string = True
                i += 3
                continue
            if raw[i] in "'\"`":
                string_delim = raw[i]
                has_string = True
                i += 1
                continue

            code.append(raw[i])
            i += 1

        analysis = "".join(code)
        has_code = bool(analysis.strip()) or has_string
        comment_only = has_comment and not has_code
        records.append({
            "raw": raw,
            "analysis": analysis,
            "comments": "".join(comments),
            "has_comment": has_comment,
            "comment_only": comment_only,
            "has_string": has_string,
            "blank": not raw.strip(),
        })
        if comments:
            comment_parts.extend(comments)

    return records, "\n".join(comment_parts)


def _unique_names(names: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= MAX_SYMBOLS:
            break
    return out


def _python_ast_info(text: str) -> tuple[dict | None, str | None]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return None, f"Python AST unavailable: line {exc.lineno or '?'} syntax error"

    info = {
        "functions": 0,
        "lambdas": 0,
        "classes": 0,
        "types": 0,
        "imports": 0,
        "import_names": [],
        "function_names": [],
        "class_names": [],
        "conditionals": 0,
        "loops": 0,
        "exception_handlers": 0,
        "returns": 0,
        "raises": 0,
        "breaks": 0,
        "continues": 0,
        "yields": 0,
        "await_keywords": 0,
        "decision_points": 0,
        "parameters": 0,
        "docstring_lines": 0,
    }

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            info["functions"] += 1
            info["function_names"].append(node.name)
            args = node.args
            info["parameters"] += (len(args.posonlyargs) + len(args.args)
                                    + len(args.kwonlyargs)
                                    + bool(args.vararg) + bool(args.kwarg))
            _docstring_lines(node, info)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            info["functions"] += 1
            info["function_names"].append(node.name)
            args = node.args
            info["parameters"] += (len(args.posonlyargs) + len(args.args)
                                    + len(args.kwonlyargs)
                                    + bool(args.vararg) + bool(args.kwarg))
            _docstring_lines(node, info)
            self.generic_visit(node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            info["lambdas"] += 1
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            info["classes"] += 1
            info["class_names"].append(node.name)
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            info["imports"] += 1
            info["import_names"].extend(alias.name for alias in node.names)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            info["imports"] += 1
            module = node.module or "." * node.level
            info["import_names"].append(module)

        def visit_If(self, node: ast.If) -> None:
            info["conditionals"] += 1
            info["decision_points"] += 1
            self.generic_visit(node)

        def visit_IfExp(self, node: ast.IfExp) -> None:
            info["decision_points"] += 1
            self.generic_visit(node)

        def visit_For(self, node: ast.For) -> None:
            info["loops"] += 1
            info["decision_points"] += 1
            self.generic_visit(node)

        def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
            info["loops"] += 1
            info["decision_points"] += 1
            self.generic_visit(node)

        def visit_While(self, node: ast.While) -> None:
            info["loops"] += 1
            info["decision_points"] += 1
            self.generic_visit(node)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            info["exception_handlers"] += 1
            info["decision_points"] += 1
            self.generic_visit(node)

        def visit_BoolOp(self, node: ast.BoolOp) -> None:
            info["decision_points"] += max(0, len(node.values) - 1)
            self.generic_visit(node)

        def visit_Match(self, node: ast.Match) -> None:
            info["decision_points"] += len(node.cases)
            self.generic_visit(node)

        def visit_Return(self, node: ast.Return) -> None:
            info["returns"] += 1
            self.generic_visit(node)

        def visit_Raise(self, node: ast.Raise) -> None:
            info["raises"] += 1
            self.generic_visit(node)

        def visit_Break(self, node: ast.Break) -> None:
            info["breaks"] += 1

        def visit_Continue(self, node: ast.Continue) -> None:
            info["continues"] += 1

        def visit_Yield(self, node: ast.Yield) -> None:
            info["yields"] += 1
            self.generic_visit(node)

        def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
            info["yields"] += 1
            self.generic_visit(node)

        def visit_Await(self, node: ast.Await) -> None:
            info["await_keywords"] += 1
            self.generic_visit(node)

    Visitor().visit(tree)
    info["function_names"] = _unique_names(info["function_names"])
    info["class_names"] = _unique_names(info["class_names"])
    info["import_names"] = _unique_names(info["import_names"])
    return info, None


def _docstring_lines(node: ast.FunctionDef | ast.AsyncFunctionDef, info: dict) -> None:
    if not node.body or not isinstance(node.body[0], ast.Expr):
        return
    value = getattr(node.body[0], "value", None)
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        info["docstring_lines"] += len(value.value.splitlines())


def _generic_symbols(text: str, lang: str) -> dict:
    function_names: list[str] = []
    class_names: list[str] = []
    import_names: list[str] = []
    export_names: list[str] = []

    if lang == "Python":
        function_names = re.findall(
            r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", text, re.MULTILINE)
        class_names = re.findall(
            r"^\s*class\s+([A-Za-z_]\w*)", text, re.MULTILINE)
        import_names = re.findall(
            r"^\s*(?:from\s+([^\s]+)\s+import|import\s+([^\s#]+))",
            text, re.MULTILINE)
        import_names = [a or b for a, b in import_names]
    elif lang in {"JavaScript", "TypeScript"}:
        named = re.findall(
            r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)?", text)
        arrows = re.findall(
            r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
            r"(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>", text)
        function_names = named + arrows + ["<anonymous>"] * named.count("")
        class_names = re.findall(r"\bclass\s+([A-Za-z_$][\w$]*)", text)
        import_names = re.findall(
            r"^\s*import\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)",
            text, re.MULTILINE)
        import_names += re.findall(
            r"\brequire\(\s*['\"]([^'\"]+)", text)
        export_names = re.findall(
            r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
            r"(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)",
            text, re.MULTILINE)
    elif lang == "Go":
        function_names = re.findall(
            r"^\s*func(?:\s*\([^)]*\))?\s+([A-Za-z_]\w*)",
            text, re.MULTILINE)
        import_names = re.findall(r"^\s*import\s+[\"`]([^\"`]+)", text,
                                  re.MULTILINE)
    elif lang == "Rust":
        function_names = re.findall(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+"
                                    r"([A-Za-z_]\w*)", text, re.MULTILINE)
        class_names = re.findall(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+"
                                 r"([A-Za-z_]\w*)", text, re.MULTILINE)
        import_names = re.findall(r"^\s*(?:pub\s+)?use\s+([^;]+)", text,
                                  re.MULTILINE)
    elif lang == "Shell":
        function_names = re.findall(
            r"^\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\s*\)",
            text, re.MULTILINE)
        function_names += re.findall(r"^\s*function\s+([A-Za-z_]\w*)",
                                     text, re.MULTILINE)
    elif lang == "Ruby":
        function_names = re.findall(r"^\s*def\s+([A-Za-z_!?=]\w*[!?=]?)",
                                    text, re.MULTILINE)
        class_names = re.findall(r"^\s*class\s+([A-Za-z_]\w*)", text,
                                 re.MULTILINE)
    elif lang in {"C", "C++", "Java", "Kotlin", "PHP", "Swift"}:
        function_names = re.findall(
            r"^\s*(?:(?:public|private|protected|internal|static|inline|"
            r"virtual|final|async|extern|suspend)\s+)*"
            r"(?:[A-Za-z_][\w:<>,\[\]*&?]*\s+)+"
            r"([A-Za-z_]\w*)\s*\(", text, re.MULTILINE)
        function_names += re.findall(
            r"\bfunction\s+([A-Za-z_]\w*)", text)
        class_names = re.findall(
            r"\b(?:class|interface|struct|enum|trait)\s+([A-Za-z_]\w*)",
            text)
    else:
        function_names = re.findall(
            r"\b(?:function|fn|func)\s+([A-Za-z_]\w*)", text)
        class_names = re.findall(
            r"\b(?:class|interface|struct|enum|trait)\s+([A-Za-z_]\w*)",
            text)

    return {
        "function_count": len(function_names),
        "class_count": len(class_names),
        "function_names": _unique_names(function_names),
        "class_names": _unique_names(class_names),
        "import_names": _unique_names(import_names),
        "export_names": _unique_names(export_names),
    }


def _generic_counts(code: str, lang: str, symbols: dict) -> dict:
    word = lambda pattern: len(re.findall(pattern, code, re.MULTILINE))
    conditionals = word(r"\b(?:if|elif|unless|case|when|switch)\b")
    loops = word(r"\b(?:for|while|loop|do)\b")
    handlers = word(r"\b(?:catch|except|rescue)\b")
    logical = len(re.findall(r"&&|\|\|", code))
    if lang in {"Python", "Ruby", "Shell"}:
        logical += word(r"\b(?:and|or)\b")
    ternaries = len(re.findall(r"\?(?!=|\?|\.)", code))
    decisions = conditionals + loops + handlers + logical + ternaries
    function_count = symbols.get("function_count", len(symbols["function_names"]))
    # Anonymous JS functions are still functions even without a useful name.
    if lang in {"JavaScript", "TypeScript"}:
        function_count = word(r"\bfunction\b") + word(r"=>")
    imports = word(r"^\s*(?:import|from|use|require|include|#\s*include)\b")
    if lang == "Python":
        imports = word(r"^\s*(?:import|from)\b")
    elif lang in {"JavaScript", "TypeScript"}:
        imports = word(r"^\s*import\b") + word(r"\brequire\s*\(")
    exports = word(r"^\s*export\b")
    if lang == "Python":
        exports = word(r"^\s*__all__\s*=")
    declarations = (
        function_count + symbols.get("class_count", len(symbols["class_names"]))
        + word(r"^\s*(?:const|let|var|type|interface|struct|enum)\b")
    )
    calls = len(re.findall(r"\b[A-Za-z_$][\w$]*\s*\(", code))
    calls = max(0, calls - function_count - conditionals - loops - handlers)
    return {
        "functions": function_count,
        "classes": symbols.get("class_count", len(symbols["class_names"])),
        "types": word(r"\b(?:struct|enum|trait|interface|type)\b"),
        "imports": imports,
        "exports": exports,
        "declarations": declarations,
        "call_sites": calls,
        "parameters": 0,
        "lambdas": word(r"\blambda\b"),
        "docstring_lines": 0,
        "conditionals": conditionals,
        "loops": loops,
        "exception_handlers": handlers,
        "returns": word(r"\breturn\b"),
        "raises": word(r"\b(?:raise|throw)\b"),
        "breaks": word(r"\bbreak\b"),
        "continues": word(r"\bcontinue\b"),
        "yields": word(r"\byield\b"),
        "async_keywords": word(r"\basync\b"),
        "await_keywords": word(r"\bawait\b"),
        "decision_points": decisions,
    }


def _halstead(code: str) -> dict:
    operators: list[str] = []
    operands: list[str] = []
    for match in _TOKEN_RE.finditer(code):
        kind = match.lastgroup
        value = match.group(0)
        if kind == "op":
            operators.append(value)
        elif kind == "number":
            operands.append(value)
        elif value.lower() in _OPERATOR_WORDS:
            operators.append(value.lower())
        else:
            operands.append(value)
    total_operators = len(operators)
    total_operands = len(operands)
    unique_operators = len(set(operators))
    unique_operands = len(set(operands))
    vocabulary = unique_operators + unique_operands
    length = total_operators + total_operands
    volume = length * math.log2(vocabulary) if vocabulary > 1 else 0
    difficulty = 0
    if unique_operands:
        difficulty = (unique_operators / 2) * (total_operands / unique_operands)
    effort = difficulty * volume
    estimated_bugs = volume ** (2 / 3) / 3000 if volume else 0
    return {
        "operators": total_operators,
        "operands": total_operands,
        "unique_operators": unique_operators,
        "unique_operands": unique_operands,
        "halstead": {
            "vocabulary": vocabulary,
            "length": length,
            "volume": round(volume, 2),
            "difficulty": round(difficulty, 2),
            "effort": round(effort, 2),
            "estimated_bugs": round(estimated_bugs, 4),
        },
    }


def _complexity_rating(value: int) -> str:
    if value <= 10:
        return "low"
    if value <= 20:
        return "moderate"
    if value <= 40:
        return "high"
    return "very high"


def analyze_source(text: str, lang: str = "Other") -> dict:
    """Analyze source text without importing or executing it."""
    records, comment_text = _lex_lines(text, lang)
    lines = [r["raw"] for r in records]
    total_lines = len(lines)
    blank_lines = sum(1 for r in records if r["blank"])
    comment_lines = sum(1 for r in records if r["has_comment"])
    comment_only_lines = sum(1 for r in records if r["comment_only"])
    inline_comment_lines = comment_lines - comment_only_lines
    string_lines = sum(1 for r in records if r["has_string"])
    code_lines = total_lines - blank_lines - comment_only_lines
    comment_blocks = 0
    in_comment_block = False
    for record in records:
        if record["comment_only"] and not in_comment_block:
            comment_blocks += 1
        in_comment_block = record["comment_only"]

    code = "\n".join(r["analysis"] for r in records)
    symbols = _generic_symbols(code, lang)
    if lang in {"JavaScript", "TypeScript"}:
        js_imports = re.findall(
            r"^\s*import\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)",
            text, re.MULTILINE)
        js_imports += re.findall(
            r"\brequire\(\s*['\"]([^'\"]+)", text)
        symbols["import_names"] = _unique_names(js_imports)
    counts = _generic_counts(code, lang, symbols)
    ast_info = None
    parse_error = None
    analysis_kind = "lexical heuristic estimates"
    if lang == "Python":
        ast_info, parse_error = _python_ast_info(text)
        if ast_info:
            analysis_kind = "Python AST + lexical metrics"
            for key in (
                "functions", "classes", "types", "imports", "conditionals",
                "loops", "exception_handlers", "returns", "raises", "breaks",
                "continues", "yields", "await_keywords", "decision_points",
            ):
                counts[key] = ast_info[key]
            counts["parameters"] = ast_info["parameters"]
            counts["lambdas"] = ast_info["lambdas"]
            counts["docstring_lines"] = ast_info["docstring_lines"]
            symbols["function_names"] = ast_info["function_names"]
            symbols["class_names"] = ast_info["class_names"]
            symbols["import_names"] = ast_info["import_names"]
            counts["declarations"] = (counts["functions"] + counts["classes"])
            counts["async_keywords"] = len(re.findall(r"\basync\b", code))

    halstead = _halstead(code)
    complexity = 1 + counts["decision_points"]
    chars = len(text)
    lengths = [len(line) for line in lines]
    max_line_length = max(lengths, default=0)
    avg_line_length = round(sum(lengths) / total_lines, 1) if total_lines else 0
    max_indent_level = 0
    max_indent_spaces = 0
    tab_indented_lines = 0
    space_indented_lines = 0
    for line in lines:
        prefix = re.match(r"^[ \t]*", line).group(0)
        if not prefix:
            continue
        if "\t" in prefix:
            tab_indented_lines += 1
        if " " in prefix:
            space_indented_lines += 1
        columns = len(prefix.expandtabs(4))
        max_indent_spaces = max(max_indent_spaces, columns)
        max_indent_level = max(max_indent_level, math.ceil(columns / 4))

    brace_depth = 0
    max_brace_depth = 0
    for record in records:
        opens = record["analysis"].count("{")
        closes = record["analysis"].count("}")
        brace_depth = max(0, brace_depth - closes)
        max_brace_depth = max(max_brace_depth, brace_depth)
        brace_depth += opens
        max_brace_depth = max(max_brace_depth, brace_depth)

    markers = Counter(key.upper() for key in re.findall(
        r"\b(TODO|FIXME|HACK|XXX|BUG|NOTE)\b", comment_text, re.IGNORECASE))
    todo_count = sum(markers.values())
    comment_ratio = round((comment_lines / total_lines) * 100, 1) if total_lines else 0
    blank_ratio = round((blank_lines / total_lines) * 100, 1) if total_lines else 0
    maintainability = None
    volume = halstead["halstead"]["volume"]
    if volume and code_lines:
        raw_mi = (171 - 3.42 * math.log(volume)
                  - 0.23 * complexity - 16.2 * math.log(code_lines))
        maintainability = round(max(0, min(100, raw_mi * 100 / 171)), 1)

    if "\r\n" in text:
        newline_style = "CRLF"
    elif "\n" in text:
        newline_style = "LF"
    elif "\r" in text:
        newline_style = "CR"
    else:
        newline_style = "none"

    result = empty_metrics()
    result.update({
        "analyzed": True,
        "analysis": analysis_kind,
        "parse_error": parse_error,
        "total_lines": total_lines,
        "code_lines": code_lines,
        "blank_lines": blank_lines,
        "comment_lines": comment_lines,
        "comment_only_lines": comment_only_lines,
        "inline_comment_lines": inline_comment_lines,
        "comment_blocks": comment_blocks,
        "string_lines": string_lines,
        "characters": chars,
        "unicode_characters": sum(1 for char in text if ord(char) > 127),
        "words": len(re.findall(r"\b[\w']+\b", text, re.UNICODE)),
        "tokens": halstead["halstead"]["length"],
        "operators": halstead["operators"],
        "operands": halstead["operands"],
        "unique_operators": halstead["unique_operators"],
        "unique_operands": halstead["unique_operands"],
        **counts,
        "max_nesting_depth": max(max_indent_level, max_brace_depth),
        "max_brace_depth": max_brace_depth,
        "max_indent_level": max_indent_level,
        "max_indent_spaces": max_indent_spaces,
        "cyclomatic_complexity": complexity,
        "max_line_length": max_line_length,
        "avg_line_length": avg_line_length,
        "trailing_whitespace_lines": sum(
            1 for line in lines if _TRAILING_WS_RE.search(line)),
        "tab_indented_lines": tab_indented_lines,
        "space_indented_lines": space_indented_lines,
        "final_newline": text.endswith(("\n", "\r")),
        "newline_style": newline_style,
        "todo_count": todo_count,
        "todo_markers": dict(sorted(markers.items())),
        "comment_ratio": comment_ratio,
        "blank_ratio": blank_ratio,
        "function_names": symbols["function_names"],
        "class_names": symbols["class_names"],
        "import_names": symbols["import_names"],
        "export_names": symbols["export_names"],
        "halstead": halstead["halstead"],
        "maintainability_index": maintainability,
        "complexity_rating": _complexity_rating(complexity),
    })
    return result


def aggregate(metrics_list: list[dict]) -> dict:
    """Combine file metrics for the files section without fake complexity sums."""
    analyzed = [m for m in metrics_list if m.get("analyzed")]
    out = empty_metrics("aggregate")
    out.update({
        "analyzed": bool(analyzed),
        "analysis": "aggregate of per-file metrics",
        "files_analyzed": len(analyzed),
    })
    sum_fields = (
        "total_lines", "code_lines", "blank_lines", "comment_lines",
        "comment_only_lines", "inline_comment_lines", "comment_blocks",
        "string_lines", "characters", "unicode_characters", "words", "tokens",
        "operators", "operands", "unique_operators", "unique_operands",
        "functions", "classes", "types", "imports", "exports", "declarations",
        "parameters", "lambdas", "docstring_lines",
        "call_sites", "conditionals", "loops", "exception_handlers", "returns",
        "raises", "breaks", "continues", "yields", "async_keywords",
        "await_keywords", "decision_points", "trailing_whitespace_lines",
        "tab_indented_lines", "space_indented_lines", "todo_count",
    )
    for field in sum_fields:
        out[field] = sum(int(m.get(field) or 0) for m in analyzed)
    out["cyclomatic_complexity"] = sum(
        int(m.get("cyclomatic_complexity") or 1) for m in analyzed)
    out["max_nesting_depth"] = max(
        (int(m.get("max_nesting_depth") or 0) for m in analyzed), default=0)
    out["max_brace_depth"] = max(
        (int(m.get("max_brace_depth") or 0) for m in analyzed), default=0)
    out["max_indent_level"] = max(
        (int(m.get("max_indent_level") or 0) for m in analyzed), default=0)
    out["max_line_length"] = max(
        (int(m.get("max_line_length") or 0) for m in analyzed), default=0)
    if out["total_lines"]:
        out["avg_line_length"] = round(
            sum((m.get("avg_line_length") or 0) * (m.get("total_lines") or 0)
                for m in analyzed) / out["total_lines"], 1)
        out["comment_ratio"] = round(
            out["comment_lines"] / out["total_lines"] * 100, 1)
        out["blank_ratio"] = round(
            out["blank_lines"] / out["total_lines"] * 100, 1)
    out["todo_markers"] = dict(sorted(
        (key, sum((m.get("todo_markers") or {}).get(key, 0)
                  for m in analyzed))
        for key in {key for m in analyzed for key in (m.get("todo_markers") or {})}
    ))
    for key in ("vocabulary", "length", "volume", "difficulty", "effort",
                "estimated_bugs"):
        out["halstead"][key] = round(sum(
            float((m.get("halstead") or {}).get(key) or 0)
            for m in analyzed), 4)
    if analyzed:
        weighted_mi = sum(
            (m.get("maintainability_index") or 0) * (m.get("total_lines") or 0)
            for m in analyzed)
        out["maintainability_index"] = round(
            weighted_mi / max(1, out["total_lines"]), 1)
    return out
