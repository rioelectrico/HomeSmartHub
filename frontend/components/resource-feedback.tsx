import { LoaderCircle } from "lucide-react";

export function ResourceFeedback({ loading, error, onRetry }: { loading: boolean; error?: string; onRetry: () => Promise<void> }) {
  if (error) return <div className="resource-error"><p role="alert" className="form-error">{error} Los datos anteriores pueden estar desactualizados.</p><button className="button button--secondary" onClick={() => void onRetry()}>Reintentar</button></div>;
  if (loading) return <p role="status" className="loading-message"><LoaderCircle className="spin" aria-hidden="true" />Cargando datos…</p>;
  return null;
}
