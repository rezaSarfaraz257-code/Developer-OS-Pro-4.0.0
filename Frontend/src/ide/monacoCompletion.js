const COMPLETION_KIND_NAMES = [
  "Text", "Method", "Function", "Constructor", "Field", "Variable", "Class",
  "Struct", "Interface", "Module", "Property", "Event", "Operator", "Unit",
  "Value", "Constant", "Enum", "EnumMember", "Keyword", "Snippet", "Color",
  "File", "Reference", "Folder", "TypeParameter",
];

export function nextLspDocumentVersion(versions, uri) {
  if (!versions || typeof versions.get !== "function" || typeof versions.set !== "function"
    || typeof uri !== "string" || !uri) return 2;
  const current = versions.get(uri);
  const next = Number.isSafeInteger(current) && current >= 1 && current < Number.MAX_SAFE_INTEGER
    ? current + 1
    : 2;
  versions.set(uri, next);
  return next;
}

function toMonacoRange(range) {
  if (!range?.start || !range?.end) return null;
  const coordinates = [
    range.start.line, range.start.character, range.end.line, range.end.character,
  ].map(Number);
  if (coordinates.some(value => !Number.isInteger(value) || value < 0)) return null;
  const [startLine, startColumn, endLine, endColumn] = coordinates;
  if (endLine < startLine || (endLine === startLine && endColumn < startColumn)) return null;
  return {
    startLineNumber: startLine + 1,
    startColumn: startColumn + 1,
    endLineNumber: endLine + 1,
    endColumn: endColumn + 1,
  };
}

export function mapLspCompletionItem(item, monaco, model, position) {
  if (!item || !monaco?.languages?.CompletionItemKind || !model || !position) return null;

  const word = model.getWordUntilPosition(position);
  const textEdit = item.textEdit;
  const lspRange = textEdit?.replace || textEdit?.range || textEdit?.insert;
  const range = toMonacoRange(lspRange) || {
    startLineNumber: position.lineNumber,
    startColumn: word.startColumn,
    endLineNumber: position.lineNumber,
    endColumn: word.endColumn,
  };
  const completion = {
    label: typeof item.label === "string" ? item.label : String(item.label ?? ""),
    kind: COMPLETION_KIND_NAMES[item.kind - 1]
      ? monaco.languages.CompletionItemKind[COMPLETION_KIND_NAMES[item.kind - 1]]
      : monaco.languages.CompletionItemKind.Text,
    detail: item.detail || "",
    insertText: textEdit?.newText ?? item.insertText ?? String(item.label ?? ""),
    range,
  };

  if (item.filterText) completion.filterText = item.filterText;
  if (item.sortText) completion.sortText = item.sortText;
  if (item.preselect === true) completion.preselect = true;
  if (Array.isArray(item.commitCharacters)) completion.commitCharacters = item.commitCharacters;
  if (item.insertTextFormat === 2) {
    completion.insertTextRules = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;
  }
  if (item.documentation) {
    completion.documentation = typeof item.documentation === "string"
      ? item.documentation
      : item.documentation.value || "";
  }
  if (Array.isArray(item.additionalTextEdits)) {
    completion.additionalTextEdits = item.additionalTextEdits
      .map(edit => {
        const editRange = toMonacoRange(edit.range);
        return editRange ? { range: editRange, text: edit.newText || "" } : null;
      })
      .filter(Boolean);
  }
  if (item.command?.command) {
    completion.command = {
      id: item.command.command,
      title: item.command.title || item.command.command,
      arguments: item.command.arguments,
    };
  }
  return completion;
}
export function getUnsupportedLspResourceOperations(workspaceEdit) {
  if (!Array.isArray(workspaceEdit?.documentChanges)) return [];
  // LSP documentChanges is a union of TextDocumentEdit and resource operations.
  // Reject malformed/unknown entries too: silently dropping one would partially apply an edit.
  return workspaceEdit.documentChanges.filter(change => {
    if (change?.kind === "create" || change?.kind === "rename" || change?.kind === "delete") return true;
    return !(change?.textDocument && typeof change.textDocument.uri === "string"
      && change.textDocument.uri.length > 0 && Array.isArray(change.edits));
  });
}

export function mapLspWorkspaceEdit(workspaceEdit, monaco) {
  if (!workspaceEdit || !monaco?.Uri?.parse) return { edits: [] };
  const edits = [];
  const seen = new Set();
  const append = (uri, items) => {
    if (typeof uri !== "string" || !Array.isArray(items)) return;
    let resource;
    try {
      resource = monaco.Uri.parse(uri);
    } catch {
      return;
    }
    for (const item of items) {
      const range = toMonacoRange(item?.range);
      if (!range || typeof item?.newText !== "string") continue;
      edits.push({ resource, textEdit: { range, text: item.newText } });
    }
  };

  if (workspaceEdit.changes && typeof workspaceEdit.changes === "object") {
    for (const [uri, items] of Object.entries(workspaceEdit.changes)) {
      seen.add(uri);
      append(uri, items);
    }
  }
  if (Array.isArray(workspaceEdit.documentChanges)) {
    for (const change of workspaceEdit.documentChanges) {
      const uri = change?.textDocument?.uri;
      if (typeof uri !== "string" || seen.has(uri)) continue;
      append(uri, change.edits);
    }
  }
  return { edits };
}
export function mapLspLocations(result, monaco) {
  if (!monaco?.Uri?.parse) return [];
  const locations = Array.isArray(result) ? result : result ? [result] : [];
  return locations.flatMap(location => {
    const uri = location?.uri || location?.targetUri;
    const range = location?.range || location?.targetSelectionRange || location?.targetRange;
    const mappedRange = toMonacoRange(range);
    if (typeof uri !== "string" || !mappedRange) return [];
    try {
      return [{ uri: monaco.Uri.parse(uri), range: mappedRange }];
    } catch {
      return [];
    }
  });
}
export function mapLspDiagnostics(diagnostics, monaco) {
  if (!Array.isArray(diagnostics) || !monaco?.MarkerSeverity) return [];
  const severity = {
    1: monaco.MarkerSeverity.Error,
    2: monaco.MarkerSeverity.Warning,
    3: monaco.MarkerSeverity.Info,
    4: monaco.MarkerSeverity.Hint,
  };
  return diagnostics.flatMap(diagnostic => {
    const range = toMonacoRange(diagnostic?.range);
    if (!range || typeof diagnostic?.message !== "string") return [];
    const marker = {
      ...range,
      severity: severity[diagnostic.severity] ?? monaco.MarkerSeverity.Info,
      message: diagnostic.message,
      source: typeof diagnostic.source === "string" ? diagnostic.source : "LSP",
    };
    if (typeof diagnostic.code === "string" || typeof diagnostic.code === "number") {
      marker.code = String(diagnostic.code);
    }
    return [marker];
  });
}
export function mapLspDocumentHighlights(highlights, monaco) {
  if (!Array.isArray(highlights) || !monaco?.languages?.DocumentHighlightKind) return [];
  const kinds = monaco.languages.DocumentHighlightKind;
  const kindMap = {
    1: kinds.Text,
    2: kinds.Read,
    3: kinds.Write,
  };
  return highlights.flatMap(highlight => {
    const range = toMonacoRange(highlight?.range);
    if (!range) return [];
    return [{
      range,
      kind: kindMap[highlight.kind] ?? kinds.Text,
    }];
  });
}
export function mapLspInlayHints(hints, monaco) {
  if (!Array.isArray(hints) || !monaco?.languages?.InlayHintKind) return [];
  const kinds = monaco.languages.InlayHintKind;
  const kindMap = { 1: kinds.Type, 2: kinds.Parameter };
  return hints.flatMap(hint => {
    const line = Number(hint?.position?.line);
    const character = Number(hint?.position?.character);
    const label = typeof hint?.label === "string"
      ? hint.label
      : Array.isArray(hint?.label)
        ? hint.label.map(part => typeof part?.value === "string" ? part.value : "").join("")
        : "";
    if (!Number.isInteger(line) || line < 0 || !Number.isInteger(character) || character < 0 || !label) return [];
    const mapped = {
      position: { lineNumber: line + 1, column: character + 1 },
      label,
      paddingLeft: hint.paddingLeft === true,
      paddingRight: hint.paddingRight === true,
    };
    if (kindMap[hint.kind] !== undefined) mapped.kind = kindMap[hint.kind];
    if (typeof hint.tooltip === "string") mapped.tooltip = hint.tooltip;
    else if (typeof hint.tooltip?.value === "string") mapped.tooltip = hint.tooltip.value;
    return [mapped];
  });
}

export function mapLspSignatureHelp(result) {
  const signatures = result?.signatures;
  if (!Array.isArray(signatures)) return null;
  const mapped = signatures.flatMap(signature => {
    if (typeof signature?.label !== "string" || !signature.label) return [];
    const parameters = Array.isArray(signature.parameters)
      ? signature.parameters.flatMap(parameter => {
          if (typeof parameter?.label === "string") {
            const item = { label: parameter.label };
            if (typeof parameter.documentation === "string") item.documentation = parameter.documentation;
            else if (typeof parameter.documentation?.value === "string") item.documentation = parameter.documentation.value;
            return [item];
          }
          const label = parameter?.label;
          if (Array.isArray(label) && label.length === 2 && label.every(Number.isInteger)
            && label[0] >= 0 && label[1] > label[0] && label[1] <= signature.label.length) {
            const item = { label };
            if (typeof parameter.documentation === "string") item.documentation = parameter.documentation;
            else if (typeof parameter.documentation?.value === "string") item.documentation = parameter.documentation.value;
            return [item];
          }
          return [];
        })
      : [];
    const item = { label: signature.label, parameters };
    if (typeof signature.documentation === "string") item.documentation = signature.documentation;
    else if (typeof signature.documentation?.value === "string") item.documentation = signature.documentation.value;
    return [item];
  });
  if (!mapped.length) return null;
  const activeSignature = Number.isInteger(result.activeSignature)
    ? Math.min(mapped.length - 1, Math.max(0, result.activeSignature)) : 0;
  const activeParameters = mapped[activeSignature].parameters;
  const requestedParameter = Number.isInteger(result.activeParameter) ? result.activeParameter : 0;
  return { value: {
    signatures: mapped,
    activeSignature,
    activeParameter: activeParameters.length
      ? Math.min(activeParameters.length - 1, Math.max(0, requestedParameter)) : 0,
  } };
}
