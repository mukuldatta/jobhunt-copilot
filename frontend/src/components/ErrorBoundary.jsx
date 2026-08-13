import { Component } from 'react'

/**
 * The app had no boundary at all, so any render throw — a job whose
 * score_breakdown arrived in an unexpected shape, or useAgent() called outside
 * its provider — replaced the whole interface with a blank white page and no
 * way back except a manual reload.
 *
 * A boundary has to be a class; there is no hook equivalent.
 */
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('Render failed:', error, info?.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children

    return (
      <div className="flex min-h-screen items-center justify-center p-8">
        <div className="max-w-lg rounded border border-line p-6">
          <p className="section-label mb-2">Something broke</p>
          <h1 className="mb-3 text-lg">This screen failed to render.</h1>
          <p className="mb-4 text-base text-neutral-500">
            The agent keeps running in the background — this is a display problem, not a
            lost run. Reloading usually clears it.
          </p>
          <pre className="mb-5 max-h-40 overflow-auto rounded border border-line-soft p-3 font-mono text-xs+ text-neutral-600">
            {this.state.error?.message || String(this.state.error)}
          </pre>
          <div className="flex gap-2">
            <button onClick={() => window.location.reload()} className="btn btn-accent">
              Reload
            </button>
            <button onClick={() => this.setState({ error: null })} className="btn btn-neutral">
              Try again
            </button>
          </div>
        </div>
      </div>
    )
  }
}
