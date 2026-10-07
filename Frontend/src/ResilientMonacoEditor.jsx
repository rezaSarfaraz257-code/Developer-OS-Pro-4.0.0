import { Component } from "react";
import MonacoEditor from "./MonacoEditor";

export default class ResilientMonacoEditor extends Component {
  state = { failed: false, message: "" };

  static getDerivedStateFromError(error) {
    return { failed: true, message: error?.message || "Editor engine failed to initialize." };
  }

  componentDidCatch(error, info) {
    console.error("Developer OS Monaco editor error", error, info);
  }

  retry = () => this.setState({ failed: false, message: "" });

  render() {
    const { value = "", onChange, path = "" } = this.props;
    if (!this.state.failed) return <MonacoEditor {...this.props} />;

    return (
      <div className="dos-editor-fallback" role="alert">
        <div className="dos-editor-fallback-head">
          <strong>Editor safe mode</strong>
          <span>Monaco failed to initialize</span>
        </div>
        <textarea
          value={value}
          onChange={(e) => onChange?.(e.target.value)}
          aria-label={`Developer OS safe editor ${path || "untitled"}`}
          spellCheck={false}
        />
        <div className="dos-editor-fallback-foot">
          <span>{this.state.message}</span>
          <button type="button" onClick={this.retry}>Retry editor</button>
        </div>
      </div>
    );
  }
}
