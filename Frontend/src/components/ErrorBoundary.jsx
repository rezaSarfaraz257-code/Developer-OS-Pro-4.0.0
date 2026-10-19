import { Component } from "react";

const MAX_REPORTS = 5;
const REPORT_KEY = "developer-os-runtime-errors";

function reportRuntimeError(error, info = "") {
  try {
    const entry = {
      id: globalThis.crypto?.randomUUID?.() || `runtime-${Date.now()}`,
      message: String(error?.message || error || "Unexpected application error."),
      stack: String(error?.stack || ""),
      componentStack: String(info || ""),
      route: window.location.pathname + window.location.search,
      timestamp: new Date().toISOString(),
    };
    const previous = JSON.parse(localStorage.getItem(REPORT_KEY) || "[]");
    const reports = Array.isArray(previous) ? previous.slice(-MAX_REPORTS + 1) : [];
    reports.push(entry);
    localStorage.setItem(REPORT_KEY, JSON.stringify(reports));
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
    return {
      hasError: true,
      message: error?.message || "Unexpected application error.",
      stack: String(error?.stack || ""),
    };
  }

  componentDidCatch(error, info) {
    const errorId = reportRuntimeError(error, info?.componentStack);
    this.setState({ errorId, componentStack: String(info?.componentStack || "") });
    console.error("Developer OS UI error", error, info);
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
          <p>{this.state.message}</p>
          {this.state.errorId && (
            <small className="app-error-id" role="status">Incident {this.state.errorId}</small>
          )}
          <details className="app-error-diagnostics">
            <summary>Technical diagnostics</summary>
            <p>Route: {typeof window !== "undefined" ? window.location.pathname : "unknown"}</p>
            {this.state.stack && <pre>{this.state.stack}</pre>}
            {this.state.componentStack && <pre>{this.state.componentStack}</pre>}
          </details>
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
