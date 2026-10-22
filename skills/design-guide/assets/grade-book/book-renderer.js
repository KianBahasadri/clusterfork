"use strict";

// The CSS book supplies layout, controls, and the opening animation. Render its
// closed surfaces together so depth testing and multisampling resolve every
// shared edge, rather than compositing separately rasterized CSS rectangles.
window.createBookRenderer = ({ stage, book, image }) => {
  const canvas = document.createElement("canvas");
  const gl = canvas.getContext("webgl2", { antialias: true, alpha: true, depth: true });
  if (!gl) return null;
  canvas.className = "book-renderer";
  canvas.setAttribute("aria-hidden", "true");
  canvas.hidden = true;
  stage.append(canvas);

  const vertexSource = `#version 300 es
    in vec3 position;
    in vec3 normal;
    in vec2 uv;
    in float material;
    uniform mat3 rotation;
    uniform float zoom;
    uniform float perspective;
    uniform vec2 origin;
    uniform vec2 viewport;
    out vec2 texcoord;
    out vec3 surfaceNormal;
    flat out int surface;
    void main() {
      vec3 p = rotation * position * zoom;
      float w = 1.0 - p.z / perspective;
      vec2 clip = (origin / viewport * 2.0 - 1.0) * w + p.xy / viewport * 2.0;
      gl_Position = vec4(clip.x, -clip.y, -p.z / perspective, w);
      texcoord = uv;
      surfaceNormal = rotation * normal;
      surface = int(material);
    }`;
  const fragmentSource = `#version 300 es
    precision highp float;
    uniform sampler2D cover;
    uniform sampler2D spine;
    uniform float dark;
    in vec2 texcoord;
    in vec3 surfaceNormal;
    flat in int surface;
    out vec4 color;
    void main() {
      vec3 ink = vec3(0.133, 0.243, 0.412);
      vec3 rgb;
      if (surface == 0) {
        rgb = texture(cover, texcoord).rgb;
        // The binding's shallow groove and a restrained highlight on the lip.
        float groove = 1.0 - .14 * exp(-pow((texcoord.x - .027) / .012, 2.0));
        float lip = .035 * exp(-pow((texcoord.x - .014) / .005, 2.0));
        rgb = rgb * groove + lip;
      } else if (surface == 2) {
        float depth = texcoord.y;
        vec3 edgeLow = mix(vec3(.87, .865, .83), vec3(.075, .098, .114), dark);
        vec3 edgeHigh = mix(vec3(.98, .975, .95), vec3(.125, .165, .192), dark);
        rgb = mix(edgeLow, edgeHigh, sin(depth * 3.14159));
        // Filter the page lines analytically, including when viewed edge-on.
        float frequency = 36.0;
        float detail = 1.0 - smoothstep(.15, .65, fwidth(depth * frequency));
        rgb -= mix(.018, .012, dark) * detail * (.5 + .5 * cos(depth * frequency * 6.28318));
        rgb *= .90 + .10 * smoothstep(0.0, .05, texcoord.x);
      } else if (surface == 3) {
        rgb = texture(spine, texcoord).rgb;
      } else if (surface == 4) {
        rgb = ink * .62;
      } else {
        rgb = ink * mix(.92, 1.02, texcoord.x);
        if (surfaceNormal.z < -.5) {
          float groove = 1.0 - .14 * exp(-pow((texcoord.x - .027) / .012, 2.0));
          float lip = .035 * exp(-pow((texcoord.x - .014) / .005, 2.0));
          rgb = rgb * groove + lip;
        }
      }
      float light = .94 + .06 * max(0.0, dot(normalize(surfaceNormal), normalize(vec3(-.3, -.45, 1.0))));
      color = vec4(rgb * light, 1.0);
    }`;

  let program;
  let buffer;
  let locations;
  let coverTexture;
  let spineTexture;
  let vertexCount = 0;
  let meshSize = "";
  let available = false;
  let enabled = false;
  let frame = 0;
  let renderUntil = 0;
  let failed = false;

  function shader(type, source) {
    const result = gl.createShader(type);
    gl.shaderSource(result, source);
    gl.compileShader(result);
    if (!gl.getShaderParameter(result, gl.COMPILE_STATUS)) {
      gl.deleteShader(result);
      throw new Error("Book shader compilation failed");
    }
    return result;
  }

  function texture(source) {
    const result = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, result);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.generateMipmap(gl.TEXTURE_2D);
    const anisotropy = gl.getExtension("EXT_texture_filter_anisotropic");
    if (anisotropy) gl.texParameterf(gl.TEXTURE_2D, anisotropy.TEXTURE_MAX_ANISOTROPY_EXT,
      Math.min(16, gl.getParameter(anisotropy.MAX_TEXTURE_MAX_ANISOTROPY_EXT)));
    return result;
  }

  function initialize() {
    if (failed || available || !image.complete || !image.naturalWidth || gl.isContextLost()) return;
    try {
      const vertex = shader(gl.VERTEX_SHADER, vertexSource);
      const fragment = shader(gl.FRAGMENT_SHADER, fragmentSource);
      program = gl.createProgram();
      gl.attachShader(program, vertex);
      gl.attachShader(program, fragment);
      gl.linkProgram(program);
      gl.deleteShader(vertex);
      gl.deleteShader(fragment);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error("Book shader link failed");
      gl.useProgram(program);
      locations = Object.fromEntries(["rotation", "zoom", "perspective", "origin", "viewport", "cover", "spine", "dark"]
        .map(name => [name, gl.getUniformLocation(program, name)]));
      buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      for (const [name, size, offset] of [["position", 3, 0], ["normal", 3, 3], ["uv", 2, 6], ["material", 1, 8]]) {
        const attribute = gl.getAttribLocation(program, name);
        gl.enableVertexAttribArray(attribute);
        gl.vertexAttribPointer(attribute, size, gl.FLOAT, false, 36, offset * 4);
      }
      // Upload the original once; mipmaps and anisotropic filtering preserve
      // the type at both small sizes and grazing angles without shimmering.
      const limit = gl.getParameter(gl.MAX_TEXTURE_SIZE);
      let artwork = image;
      if (!image.classList.contains("cover-flat") || Math.max(image.naturalWidth, image.naturalHeight) > limit) {
        artwork = document.createElement("canvas");
        const scale = Math.min(1, limit / Math.max(image.naturalWidth, image.naturalHeight));
        artwork.width = Math.floor(image.naturalWidth * scale);
        artwork.height = Math.floor(image.naturalHeight * scale);
        const context = artwork.getContext("2d");
        context.imageSmoothingQuality = "high";
        if (image.classList.contains("cover-flat")) context.drawImage(image, 0, 0, artwork.width, artwork.height);
        else context.drawImage(image, image.naturalWidth * .0466 / 1.0907, image.naturalHeight * .0071 / 1.0454,
          image.naturalWidth / 1.0907, image.naturalHeight / 1.0454, 0, 0, artwork.width, artwork.height);
      }
      coverTexture = texture(artwork);
      const label = document.createElement("canvas");
      label.width = 128;
      label.height = 2048;
      const context = label.getContext("2d");
      const gradient = context.createLinearGradient(0, 0, 128, 0);
      gradient.addColorStop(0, "#1f3961");
      gradient.addColorStop(.5, "#284675");
      gradient.addColorStop(1, "#223e69");
      context.fillStyle = gradient;
      context.fillRect(0, 0, 128, 2048);
      context.translate(64, 120);
      context.rotate(Math.PI / 2);
      context.fillStyle = "#fff";
      context.font = '40px "IBM Plex Sans", sans-serif';
      context.fillText("The GRADE Book", 0, 12);
      spineTexture = texture(label);
      if (gl.getError() !== gl.NO_ERROR) throw new Error("Book texture upload failed");
      gl.uniform1i(locations.cover, 0);
      gl.uniform1i(locations.spine, 1);
      gl.enable(gl.DEPTH_TEST);
      gl.clearColor(0, 0, 0, 0);
      available = true;
      meshSize = "";
      invalidate();
    } catch {
      failed = true;
      suspend();
    }
  }

  function mesh(width, height) {
    const data = [];
    const vertex = (point, normal, uv, material) => data.push(...point, ...normal, ...uv, material);
    const quad = (points, normal, uv, material) => {
      for (const i of [0, 1, 2, 0, 2, 3]) vertex(points[i], normal, uv[i], material);
    };
    const uv = point => [point[0] / width + .5, point[1] / height + .5];
    const halfSpan = 12, bulge = 5.6, shoulder = 4.8, thickness = 2;
    const spineStart = -width / 2 + bulge - 1.2;
    const board = (bottom, top, frontMaterial, innerSide) => {
      const ring = [[spineStart, -height / 2]];
      const radius = 4;
      for (const [cx, cy, start] of [[width / 2 - radius, -height / 2 + radius, -Math.PI / 2],
        [width / 2 - radius, height / 2 - radius, 0]]) {
        for (let i = 0; i <= 12; i++) {
          const angle = start + i / 12 * Math.PI / 2;
          ring.push([cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius]);
        }
      }
      ring.push([spineStart, height / 2]);
      const topMaterial = innerSide === "top" ? 4 : frontMaterial;
      const bottomMaterial = innerSide === "bottom" ? 4 : 1;
      for (let i = 0; i < ring.length; i++) {
        const a = ring[i], b = ring[(i + 1) % ring.length];
        for (const [z, normal, material] of [[top, [0, 0, 1], topMaterial], [bottom, [0, 0, -1], bottomMaterial]]) {
          vertex([0, 0, z], normal, [.5, .5], material);
          vertex([...a, z], normal, uv(a), material);
          vertex([...b, z], normal, uv(b), material);
        }
        if (a[0] === spineStart && b[0] === spineStart) continue;
        const dx = b[0] - a[0], dy = b[1] - a[1];
        if (Math.hypot(dx, dy) < .001) continue;
        const length = Math.hypot(dx, dy);
        const uvA = uv(a), uvB = uv(b);
        quad([[...a, bottom], [...b, bottom], [...b, top], [...a, top]], [dy / length, -dx / length, 0],
          [uvA, uvB, uvB, uvA], 1);
      }
    };
    board(-22, -20, 1, "top");
    board(0, 2, 0, "bottom");

    const zMid = -10;
    const crown = (bulge * bulge - shoulder * shoulder + (halfSpan - shoulder) ** 2) / (2 * (bulge - shoulder));
    const transition = Math.asin((halfSpan - shoulder) / (crown - shoulder));
    const angles = [];
    for (let i = 0; i <= 10; i++) angles.push(-Math.PI / 2 + i / 10 * (Math.PI / 2 - transition));
    for (let i = 1; i < 16; i++) angles.push(-transition + i / 16 * (2 * transition));
    for (let i = 0; i <= 10; i++) angles.push(transition + i / 10 * (Math.PI / 2 - transition));

    const spineProfile = angles.map((angle, index) => {
      const nx = -Math.cos(angle), nz = Math.sin(angle);
      const [cx, cz, radius] = angle < -transition
        ? [spineStart, zMid - (halfSpan - shoulder), shoulder]
        : angle > transition
          ? [spineStart, zMid + (halfSpan - shoulder), shoulder]
          : [spineStart - bulge + crown, zMid, crown];
      return {
        u: index / (angles.length - 1),
        xOut: cx + nx * radius,
        zOut: cz + nz * radius,
        xIn: cx + nx * (radius - thickness),
        zIn: cz + nz * (radius - thickness),
        nx,
        nz
      };
    });

    const boardTop = -height / 2, boardBottom = height / 2;
    const top = -height / 2 + 2.2, bottom = height / 2 - 2.2;
    const right = width / 2 - 3;

    // The spine faces left: its left edge is the back board when seen from
    // outside. Reversing these U coordinates would mirror every letter.
    for (let i = 0; i < spineProfile.length - 1; i++) {
      const a = spineProfile[i], b = spineProfile[i + 1];
      const p0 = [b.xOut, boardTop, b.zOut], p1 = [a.xOut, boardTop, a.zOut];
      const p2 = [a.xOut, boardBottom, a.zOut], p3 = [b.xOut, boardBottom, b.zOut];
      const nB = [b.nx, 0, b.nz], nA = [a.nx, 0, a.nz];
      const uv0 = [b.u, 0], uv1 = [a.u, 0], uv2 = [a.u, 1], uv3 = [b.u, 1];
      for (const [p, n, t] of [[p0, nB, uv0], [p1, nA, uv1], [p2, nA, uv2], [p0, nB, uv0], [p2, nA, uv2], [p3, nB, uv3]]) {
        vertex(p, n, t, 3);
      }
      const uvOutA = uv([a.xOut, boardTop]), uvOutB = uv([b.xOut, boardTop]);
      const uvInA = uv([a.xIn, boardTop]), uvInB = uv([b.xIn, boardTop]);
      quad([[a.xOut, boardTop, a.zOut], [a.xIn, boardTop, a.zIn], [b.xIn, boardTop, b.zIn], [b.xOut, boardTop, b.zOut]],
        [0, -1, 0], [uvOutA, uvInA, uvInB, uvOutB], 1);
      quad([[b.xOut, boardBottom, b.zOut], [b.xIn, boardBottom, b.zIn], [a.xIn, boardBottom, a.zIn], [a.xOut, boardBottom, a.zOut]],
        [0, 1, 0], [uvOutB, uvInB, uvInA, uvOutA], 1);
      const nIn = [-a.nx, 0, -a.nz];
      quad([[a.xIn, boardTop, a.zIn], [b.xIn, boardTop, b.zIn], [b.xIn, top, b.zIn], [a.xIn, top, a.zIn]],
        nIn, [[a.u, 1], [b.u, 1], [b.u, 0], [a.u, 0]], 4);
      quad([[b.xIn, bottom, b.zIn], [b.xIn, boardBottom, b.zIn], [a.xIn, boardBottom, a.zIn], [a.xIn, bottom, a.zIn]],
        nIn, [[b.u, 0], [b.u, 1], [a.u, 1], [a.u, 0]], 4);

      const depthA = (a.zIn + 20) / 20, depthB = (b.zIn + 20) / 20;
      quad([[a.xIn, top, a.zIn], [right, top, a.zIn], [right, top, b.zIn], [b.xIn, top, b.zIn]],
        [0, -1, 0], [[0, depthA], [1, depthA], [1, depthB], [0, depthB]], 2);
      quad([[b.xIn, bottom, b.zIn], [right, bottom, b.zIn], [right, bottom, a.zIn], [a.xIn, bottom, a.zIn]],
        [0, 1, 0], [[0, depthB], [1, depthB], [1, depthA], [0, depthA]], 2);
    }

    const pageUv = [[0, 0], [1, 0], [1, 1], [0, 1]];
    quad([[right, top, -20], [right, bottom, -20], [right, bottom, 0], [right, top, 0]], [1, 0, 0], pageUv, 2);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
    vertexCount = data.length / 9;
  }

  function draw() {
    const style = getComputedStyle(book);
    const stageStyle = getComputedStyle(stage);
    const width = book.offsetWidth, height = book.offsetHeight;
    const zoom = parseFloat(stageStyle.getPropertyValue("--book-scale")) || 1;
    const matrix = new DOMMatrix(style.transform);
    const rotation = new Float32Array([matrix.m11, matrix.m12, matrix.m13,
      matrix.m21, matrix.m22, matrix.m23, matrix.m31, matrix.m32, matrix.m33]);
    const perspective = parseFloat(stageStyle.perspective) || 1800;
    const center = [stage.clientWidth / 2, stage.clientHeight / 2];
    const points = [];
    for (const x of [-width / 2 - 2, width / 2]) for (const y of [-height / 2, height / 2]) for (const z of [-22, 2]) {
      const p = matrix.transformPoint(new DOMPoint(x * zoom, y * zoom, z * zoom));
      const w = 1 - p.z / perspective;
      points.push([center[0] + p.x / w, center[1] + p.y / w]);
    }
    const left = Math.floor(Math.min(...points.map(p => p[0]))) - 2;
    const top = Math.floor(Math.min(...points.map(p => p[1]))) - 2;
    const wide = Math.ceil(Math.max(...points.map(p => p[0]))) - left + 2;
    const high = Math.ceil(Math.max(...points.map(p => p[1]))) - top + 2;
    // Extra physical samples smooth silhouettes even on a standard-density
    // display. Bound memory by the projected book, not the entire viewport.
    const density = Math.min(Math.max(2, (devicePixelRatio || 1) * 1.5), 3,
      4096 / Math.max(wide, high), Math.sqrt(7_400_000 / (wide * high)));
    const pixelsWide = Math.ceil(wide * density / 64) * 64;
    const pixelsHigh = Math.ceil(high * density / 64) * 64;
    // Reuse nearby buffer sizes while rotating; allocating a new multisampled
    // framebuffer for every one-pixel change would interrupt a smooth drag.
    if (canvas.width < pixelsWide || canvas.height < pixelsHigh
        || canvas.width > pixelsWide * 1.5 || canvas.height > pixelsHigh * 1.5) {
      canvas.width = pixelsWide;
      canvas.height = pixelsHigh;
    }
    Object.assign(canvas.style, { left: left + "px", top: top + "px", width: wide + "px", height: high + "px" });
    if (meshSize !== `${width}:${height}`) {
      mesh(width, height);
      meshSize = `${width}:${height}`;
    }
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.uniformMatrix3fv(locations.rotation, false, rotation);
    gl.uniform1f(locations.zoom, zoom);
    gl.uniform1f(locations.perspective, perspective);
    gl.uniform2f(locations.origin, center[0] - left, center[1] - top);
    gl.uniform2f(locations.viewport, wide, high);
    gl.uniform1f(locations.dark, document.documentElement.dataset.theme === "dark" ? 1 : 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, coverTexture);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, spineTexture);
    gl.drawArrays(gl.TRIANGLES, 0, vertexCount);
    canvas.hidden = false;
    stage.classList.add("has-book-renderer");
  }

  function tick(now) {
    frame = 0;
    if (!enabled || !available || failed || gl.isContextLost()) return;
    draw();
    if (now < renderUntil) frame = requestAnimationFrame(tick);
  }
  function invalidate(duration = 160) {
    renderUntil = Math.max(renderUntil, performance.now() + duration);
    if (enabled && available && !failed && !frame) frame = requestAnimationFrame(tick);
  }
  function suspend() {
    enabled = false;
    cancelAnimationFrame(frame);
    frame = 0;
    canvas.hidden = true;
    stage.classList.remove("has-book-renderer");
  }
  function resume() {
    enabled = true;
    initialize();
    invalidate();
  }
  canvas.addEventListener("webglcontextlost", event => {
    event.preventDefault();
    available = false;
    canvas.hidden = true;
    stage.classList.remove("has-book-renderer");
    cancelAnimationFrame(frame);
    frame = 0;
  });
  canvas.addEventListener("webglcontextrestored", () => {
    failed = false;
    initialize();
  });
  image.addEventListener("load", initialize);
  document.fonts.ready.then(initialize);
  return { invalidate, suspend, resume };
};
