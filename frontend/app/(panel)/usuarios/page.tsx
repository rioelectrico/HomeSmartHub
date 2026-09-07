"use client";
import { useState } from "react";
import { UserPlus } from "lucide-react";
import { useHome } from "@/components/home-context";
import { ResourceFeedback } from "@/components/resource-feedback";
import { UsersTable } from "@/components/users/users-table";
import { UserDialog, type UserDialogSelection } from "@/components/users/user-dialog";
import { useResource } from "@/lib/use-resource";
import type { HomeUsers } from "@/types/api";

export default function UsersPage() {
  const home = useHome();
  const canRead = home?.permissions.includes("users.read");
  const canManage = Boolean(home?.permissions.includes("users.manage"));
  const resource = useResource<HomeUsers>(home && canRead ? `/api/homes/${home.id}/users` : null, 30000);
  const [selection, setSelection] = useState<UserDialogSelection>();
  const [message, setMessage] = useState("");
  return <section><div className="dashboard__heading"><div><h1>Usuarios</h1><p>Personas y permisos de esta vivienda.</p></div>{canRead && canManage && <button className="button button--primary" onClick={(event) => { setMessage(""); setSelection({ action: "create", trigger: event.currentTarget }); }}><UserPlus aria-hidden="true" />Crear usuario</button>}</div>
    {!home ? <p>No tenés viviendas asignadas.</p> : !canRead ? <p>Sin permiso para ver usuarios.</p> : <>
      {message && <p className="success-message" role="status">{message}</p>}
      <ResourceFeedback {...resource} onRetry={resource.refresh} />
      {resource.data && <UsersTable users={resource.data.items} canManage={canManage} onAction={(action, user, trigger) => { setMessage(""); setSelection({ action, user, trigger }); }} />}
      {selection && canManage && <UserDialog homeId={home.id} selection={selection} onClose={() => setSelection(undefined)} onSaved={(text) => { setSelection(undefined); setMessage(text); void resource.refresh(); }} />}
    </>}
  </section>;
}
