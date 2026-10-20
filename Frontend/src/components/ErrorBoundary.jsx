import { Component } from "react";

const MAX_REPORTS = 5;
const REPORT_KEY = "developer-os-runtime-errors";
const GENERIC_ERROR_MESSAGE = "An unexpected application error occurred. Try again or reload Developer OS.";

function reportRuntimeError(error) {
  try {
    // Persist only minimal incident metadata. Error messages and stack traces can
    // contain user data, source snippets, internal paths, or query-string secrets.
    const entry = {
      id: globalThis.crypto?.randomUUID?.() || `runtime-${Date.now()}`,
      type: String(error?.name || "RuntimeError").replace(/[^a-zA-Z0-9_.-]/g, "").slice(0, 60) || "RuntimeError",
      route: window.location.pathname,
      timestamp: new Date().toISOString(),
    };
    const previous = JSON.parse(localStorage.getItem(REPORT_KEY) || "[]");
    // Also sanitize older entries written by previous versions of this component.
    const reports = Array.isArray(previous)
      ? previous.slice(-MAX_REPORTS + 1).map((item) => ({
          id: String(item?.id || "legacy-incident").slice(0, 80),
          type: String(item?.type || "LegacyRuntimeError").replace(/[^a-zA-Z0-9_.-]/g, "").slice(0, 60),
          route: String(item?.route || "/").split(/[?#]/, 1)[0].slice(0, 300),
          timestamp: String(item?.timestamp || "").slice(0, 40),
        }))
      : [];
    reports.push(entry);
    localStorage.setItem(REPORT_KEY, JSON.stringify(reports.slice(-MAX_REPORTS)));
    return entry.id;
  } catch {
    return "runtime-error";
  }
}

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: "", errorId: "", stack: "", componentStack: "", resetNonce: 0 };
  }

  static getDerivedStateFromError(error) {
    const isDevelopment = import.meta.env.DEV;
    return {
      hasError: true,
      // Production users get actionable recovery guidance, not potentially sensitive
      // exception text or implementation details.
      message: isDevelopment ? (error?.message || GENERIC_ERROR_MESSAGE) : GENERIC_ERROR_MESSAGE,
      stack: isDevelopment ? String(error?.stack || "") : "",
    };
  }

  componentDidCatch(error, info) {
    const errorId = reportRuntimeError(error);
    const isDevelopment = import.meta.env.DEV;
    this.setState({
      errorId,
      componentStack: isDevelopment ? String(info?.componentStack || "") : "",
    });
    if (isDevelopment) {
      console.error("Developer OS UI error", error, info);
    } else {
      // Do not write raw exception details to the production console.
      console.error("Developer OS UI error", { incidentId: errorId });
    }
  }

  reset = () => {
    this.setState((state) => ({
      hasError: false,
      message: "",
      errorId: "",
      stack: "",
      componentStack: "",
      resetNonce: state.resetNonce + 1,
    }));
  };

  reload = () => window.location.reload();

  render() {
    if (!this.state.hasError) {
      return <div key={this.state.resetNonce}>{this.props.children}</div>;
    }

    return (
      <main className="app-error-boundary" role="alert" aria-labelledby="runtime-error-title">
        <div className="app-error-card">
          <span className="eyebrow">Workspace recovery</span>
          <h1 id="runtime-error-title">Developer OS needs a quick restart</h1>
          <p>{this.state.message || GENERIC_ERROR_MESSAGE}</p>
          {this.state.errorId && (
            <small className="app-error-id" role="status">Incident {this.state.errorId}</small>
          )}
          {import.meta.env.DEV && (
            <details className="app-error-diagnostics">
              <summary>Technical diagnostics</summary>
              <p>Route: {typeof window !== "undefined" ? window.location.pathname : "unknown"}</p>
              {this.state.stack && <pre>{this.state.stack}</pre>}
              {this.state.componentStack && <pre>{this.state.componentStack}</pre>}
            </details>
          )}
          <div className="app-error-actions">
            <button type="button" className="primary-button" onClick={this.reset}>
              Try again
            </button>
            <button type="button" className="secondary-button" onClick={this.reload}>
              Reload application
            </button>
          </div>
        </div>
      </main>
    );
  }
}
