const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

export function canRetryExplorerMutation(payload, files) {
  if (!payload || !files || typeof files !== "object" || Array.isArray(files)) return false;
  if (payload.action === "create") {
    return typeof payload.path === "string" && payload.path.length > 0 && !hasOwn(files, payload.path);
  }
  if (payload.action === "rename") {
    return typeof payload.path === "string" && payload.path.length > 0
      && typeof payload.to === "string" && payload.to.length > 0
      && hasOwn(files, payload.path) && !hasOwn(files, payload.to);
  }
  return false;
}

export function canRetryExplorerDelete(path, originalContent, files) {
  return typeof path === "string" && path.length > 0
    && typeof originalContent === "string"
    && !!files && typeof files === "object" && !Array.isArray(files)
    && hasOwn(files, path) && files[path] === originalContent;
}
