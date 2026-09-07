"use client";
import { AgentForm } from "@/components/agent/agent-form";
import { useHome } from "@/components/home-context";
import { ResourceFeedback } from "@/components/resource-feedback";
import { apiFetch } from "@/lib/api";
import { useResource } from "@/lib/use-resource";
import type { AgentConfig } from "@/types/api";

export default function AgentPage() {
  const home = useHome();
  const canView = home?.permissions.includes("agent.view");
  const path = home && canView ? `/api/homes/${home.id}/agent` : null;
  // Editable forms keep their local draft; no periodic refresh while editing.
  const resource = useResource<AgentConfig>(path, 0);
  return <section><h1>Agente</h1><p>Personalizá la recepción de visitantes.</p>
    {!home ? <p>No tenés viviendas asignadas.</p> : !canView ? <p>Sin permiso para ver el agente.</p> : <>
      <ResourceFeedback {...resource} onRetry={resource.refresh} />
      {resource.data && <AgentForm key={home.id} initialValue={resource.data} canEdit={home.permissions.includes("agent.edit")} onSave={async (values) => {
        const saved = await apiFetch<AgentConfig>(path!, { method: "PUT", body: JSON.stringify(values) });
        void resource.refresh();
        return saved;
      }} />}
    </>}
  </section>;
}
