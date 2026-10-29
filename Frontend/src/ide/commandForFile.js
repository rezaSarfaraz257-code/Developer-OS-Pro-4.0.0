function shellQuote(value) {
  // The runner executes commands through a POSIX shell. JSON.stringify is not
  // shell escaping: double-quoted strings still expand substitutions.
  return "'" + String(value).replace(/'/g, "'\\''") + "'";
}

export function commandForFile(path, files) {
  const filePath = String(path ?? "").replace(/\\/g, "/");
  const ext = filePath.split(".").pop()?.toLowerCase();

  if (
    !filePath ||
    filePath.startsWith("/") ||
    filePath.split("/").some((segment) => !segment || segment === "." || segment === "..") ||
    [...filePath].some((character) => {
      const code = character.charCodeAt(0);
      return code <= 0x1f || code === 0x7f;
    }) ||
    !Object.prototype.hasOwnProperty.call(files ?? {}, filePath)
  ) {
    return "";
  }

  const quotedPath = shellQuote(filePath);
  if (ext === "py") return `python3 ${quotedPath}`;
  if (ext === "js" || ext === "mjs" || ext === "cjs") return `node ${quotedPath}`;
  if (ext === "ts") return `npx --no-install tsx ${quotedPath}`;
  return "";
}
