import { Component, type ReactNode } from "react";

/** Catches rendering errors in a page so one broken view never blanks the whole application. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error?: Error }> {
  state: { error?: Error } = {};

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: undefined });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page">
        <section className="state inline" role="alert">
          <h2 className="state-title">This view failed to render</h2>
          <p className="text-2" style={{ margin: "4px 0 12px" }}>
            The rest of FleetPulse is unaffected. Reload the view, or choose another page.
          </p>
          <button className="btn" onClick={() => this.setState({ error: undefined })}>Reload view</button>
          {import.meta.env.DEV && (
            <details><summary>Technical details (development build)</summary><pre>{this.state.error.message}</pre></details>
          )}
        </section>
      </div>
    );
  }
}
