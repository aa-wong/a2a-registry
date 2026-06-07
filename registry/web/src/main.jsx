import { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowUpRight,
  Layers3,
  Plus,
  Trash2
} from "lucide-react";

import { Alert } from "./components/ui/alert";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import "./styles.css";

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    },
    ...options
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function formatDate(value) {
  if (!value) {
    return "Never";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short"
  }).format(date);
}

function getEndpointHost(endpoint) {
  try {
    return new URL(endpoint).host;
  } catch {
    return endpoint || "No endpoint";
  }
}

function StatusBadge({ status }) {
  const value = status || "unknown";
  return (
    <span className={`status-pill status-${value}`}>
      <span aria-hidden="true" />
      {value}
    </span>
  );
}

function TagsCell({ tags }) {
  if (!tags?.length) {
    return <Badge className="badge-muted">untagged</Badge>;
  }
  return (
    <div className="tag-list">
      {tags.map((tag) => (
        <Badge key={tag}>{tag}</Badge>
      ))}
    </div>
  );
}

function AgentRow({ agent, onDelete, busy }) {
  const linkTarget = agent.card_url || agent.endpoint;

  return (
    <tr>
      <td className="agent-cell">
        <div className="agent-name-row">
          <span className="agent-avatar" aria-hidden="true">
            {agent.name?.slice(0, 1).toUpperCase() || "A"}
          </span>
          <div>
            <strong>{agent.name}</strong>
            <span>{agent.id}</span>
          </div>
        </div>
      </td>
      <td>
        <StatusBadge status={agent.status} />
      </td>
      <td className="endpoint-cell">
        <strong>{getEndpointHost(agent.endpoint)}</strong>
        <span>{agent.endpoint}</span>
      </td>
      <td>
        <TagsCell tags={agent.tags} />
      </td>
      <td className="date-cell">{formatDate(agent.last_seen_at)}</td>
      <td className="date-cell">{formatDate(agent.registered_at)}</td>
      <td className="actions-cell">
        {linkTarget ? (
          <a
            aria-label={`Open ${agent.name} agent card`}
            className="action-link"
            href={linkTarget}
            rel="noreferrer"
            target="_blank"
          >
            <ArrowUpRight size={15} />
          </a>
        ) : null}
        <Button
          aria-label={`Delete ${agent.name}`}
          className="delete-button"
          disabled={busy}
          onClick={() => onDelete(agent)}
          size="sm"
          type="button"
          variant="destructive"
        >
          <Trash2 size={14} />
          {busy ? "Deleting" : "Delete"}
        </Button>
      </td>
    </tr>
  );
}

function LoadingRows() {
  return Array.from({ length: 3 }, (_, index) => (
    <tr className="loading-row" key={index}>
      <td><span /></td>
      <td><span /></td>
      <td><span /></td>
      <td><span /></td>
      <td><span /></td>
      <td><span /></td>
      <td><span /></td>
    </tr>
  ));
}

function App() {
  const [agents, setAgents] = useState([]);
  const [cardUrl, setCardUrl] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [lastLoadedAt, setLastLoadedAt] = useState(null);
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);

  const sortedAgents = useMemo(
    () => [...agents].sort((a, b) => a.name.localeCompare(b.name)),
    [agents]
  );

  async function loadAgents() {
    setLoading(true);
    setError(null);
    try {
      const data = await api("/api/agents");
      setAgents(data.agents);
      setLastLoadedAt(new Date());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAgents();
  }, []);

  async function registerAgent(event) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      const data = await api("/api/agents", {
        method: "POST",
        body: JSON.stringify({ card_url: cardUrl })
      });
      setCardUrl("");
      setMessage(`Registered ${data.agent.name}.`);
      await loadAgents();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function deleteAgent(agent) {
    const confirmed = window.confirm(`Delete ${agent.name} from the registry?`);
    if (!confirmed) {
      return;
    }
    setDeletingId(agent.id);
    setError(null);
    setMessage(null);
    try {
      await api(`/api/agents/${encodeURIComponent(agent.id)}`, {
        method: "DELETE"
      });
      setMessage(`Deleted ${agent.name}.`);
      await loadAgents();
    } catch (err) {
      setError(err.message);
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <main className="page">
      <header className="app-header">
        <div>
          <div className="product-mark">
            <Layers3 size={18} />
            A2A Registry
          </div>
          <h1>Agent Registry</h1>
        </div>
        <div className="header-actions">
          <span className="sync-label">
            Updated {lastLoadedAt ? formatDate(lastLoadedAt.toISOString()) : "never"}
          </span>
        </div>
      </header>

      <section className="panel register-panel" aria-labelledby="register-title">
        <div className="panel-header">
          <div>
            <h2 id="register-title">Register agent</h2>
            <p>Add an agent card URL to the local directory.</p>
          </div>
        </div>
        <form className="register-form" onSubmit={registerAgent}>
          <div className="field">
            <label htmlFor="card-url">Agent card URL</label>
            <Input
              id="card-url"
              onChange={(event) => setCardUrl(event.target.value)}
              placeholder="https://domain/.well-known/agent-card.json"
              required
              type="url"
              value={cardUrl}
            />
          </div>
          <Button disabled={submitting} type="submit">
            <Plus size={15} />
            {submitting ? "Registering" : "Register"}
          </Button>
        </form>
      </section>

      <div className="notice-stack" aria-live="polite">
        {message ? <Alert>{message}</Alert> : null}
        {error ? <Alert variant="destructive">{error}</Alert> : null}
      </div>

      <section className="panel directory-panel" aria-labelledby="directory-title">
        <div className="panel-header">
          <div>
            <h2 id="directory-title">Directory</h2>
            <p>{loading ? "Loading rows" : `${sortedAgents.length} rows`}</p>
          </div>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Agent</th>
                <th scope="col">Status</th>
                <th scope="col">Endpoint</th>
                <th scope="col">Tags</th>
                <th scope="col">Last seen</th>
                <th scope="col">Registered</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <LoadingRows />
              ) : sortedAgents.length ? (
                sortedAgents.map((agent) => (
                  <AgentRow
                    agent={agent}
                    busy={deletingId === agent.id}
                    key={agent.id}
                    onDelete={deleteAgent}
                  />
                ))
              ) : (
                <tr>
                  <td className="empty-state" colSpan="7">
                    No agents registered.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);
