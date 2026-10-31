// Bind the actual workbook KQL to the Terraform-managed schema, without Azure.
using System.Text.Json;
using System.Text.RegularExpressions;
using Kusto.Language;
using Kusto.Language.Symbols;

var directory = args.Length > 0 ? args[0] : "infra/azure/monitoring";
var schema = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "schema.json")));
var columns = schema.RootElement.EnumerateArray().Select(c =>
    c.GetProperty("name").GetString() + ":" + c.GetProperty("type").GetString());
var globals = GlobalState.Default.WithDatabase(new DatabaseSymbol("controller",
    new TableSymbol("ClusterforkController_CL", "(" + string.Join(",", columns) + ")"),
    new TableSymbol("AppRequests", "(TimeGenerated:datetime,_ResourceId:string,ItemCount:long,Success:bool)")));
var common = File.ReadAllText(Path.Combine(directory, "events.kql"));
var queries = Directory.GetFiles(directory, "*.kql")
    .Where(f => Path.GetFileNameWithoutExtension(f) != "events")
    .ToDictionary(f => Path.GetFileNameWithoutExtension(f), f => File.ReadAllText(f).Replace("__EVENTS__", common));
var template = File.ReadAllText(Path.Combine(directory, "workbook.json.tftpl"));
// These substitutions mirror Terraform's jsonencode/templatefile rendering.
var rendered = Regex.Replace(template, @"\$\{jsonencode\(queries\.(\w+)\)\}",
    match => JsonSerializer.Serialize(queries[match.Groups[1].Value]));
rendered = rendered.Replace("${jsonencode(workspace_id)}", "\"/subscriptions/fixture/workspace\"")
    .Replace("${insights_id}", "/subscriptions/fixture/insights");
var workbook = JsonDocument.Parse(rendered);
var errors = new List<string>();
var panels = workbook.RootElement.GetProperty("items").EnumerateArray()
    .Where(item => item.GetProperty("type").GetInt32() == 3).ToArray();
foreach (var selection in new[] { ("*", "*"), ("123", "2") })
{
    foreach (var panel in panels)
    {
        var name = panel.GetProperty("name").GetString();
        var query = panel.GetProperty("content").GetProperty("query").GetString()
            .Replace("{TimeRange:grain}", "1m").Replace("{TimeRange}", "> ago(1d)")
            .Replace("{RunId:escape}", selection.Item1).Replace("{Attempt:escape}", selection.Item2);
        var code = KustoCode.ParseAndAnalyze(query, globals);
        foreach (var diagnostic in code.GetDiagnostics().Where(d => d.Severity == "Error"))
            errors.Add($"{name}: {diagnostic.Code} {diagnostic.Message}");
        if (name != "historical-functions" && name != "coverage" &&
            !query.Contains("summarize arg_max(TimeGenerated, *) by EventId"))
            errors.Add($"{name}: missing logical event deduplication");
        if (name != "historical-functions" && query.Contains("AppRequests"))
            errors.Add($"{name}: historical requests mixed into controller data");
    }
}
if (errors.Count > 0)
{
    foreach (var error in errors) Console.Error.WriteLine(error);
    return 1;
}
Console.WriteLine($"Validated workbook JSON and {panels.Length} saved queries for wildcard and exact attempt selections against the ingestion schema.");
return 0;
