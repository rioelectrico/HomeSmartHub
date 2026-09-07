"use client";
import { useState } from "react";
import { KeyRound, Shield, UserRoundX } from "lucide-react";
import { roleLabels } from "@/lib/schemas/user";
import type { HomeUser } from "@/types/api";

export type UserAction = "create" | "role" | "active" | "password";
export function UsersTable({ users, canManage, onAction }: { users: HomeUser[]; canManage: boolean; onAction: (action: UserAction, user: HomeUser, trigger: HTMLButtonElement) => void }) {
  const [requestedPage, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const query = search.trim().toLocaleLowerCase("es-AR");
  const matchingUsers = users.filter((user) =>
    (!query || `${user.username} ${user.email}`.toLocaleLowerCase("es-AR").includes(query)) &&
    (!role || user.role === role) &&
    (!status || user.is_active === (status === "active")),
  );
  const availableRoles = [...new Set([...Object.keys(roleLabels), ...users.map((user) => user.role)])];
  const pages = Math.max(1, Math.ceil(matchingUsers.length / 10));
  const page = Math.min(requestedPage, pages - 1);
  function clearFilters() {
    setSearch(""); setRole(""); setStatus(""); setPage(0);
  }
  if (!users.length) return <div className="panel-card empty-state"><p>No hay usuarios en esta vivienda.</p></div>;
  return <div className="panel-card users-list">
    <div className="users-filters" role="search" aria-label="Filtrar usuarios">
      <div className="field"><label htmlFor="users-search">Buscar usuarios</label><input id="users-search" type="search" value={search} aria-describedby="users-search-hint" onChange={(event) => { setSearch(event.target.value); setPage(0); }} /><p id="users-search-hint" className="muted">Buscá por nombre o email.</p></div>
      <div className="field"><label htmlFor="users-role-filter">Filtrar por rol</label><select id="users-role-filter" value={role} onChange={(event) => { setRole(event.target.value); setPage(0); }}><option value="">Todos los roles</option>{availableRoles.map((value) => <option key={value} value={value}>{roleLabels[value] ?? value}</option>)}</select></div>
      <div className="field"><label htmlFor="users-status-filter">Filtrar por estado</label><select id="users-status-filter" value={status} onChange={(event) => { setStatus(event.target.value); setPage(0); }}><option value="">Todos los estados</option><option value="active">Activos</option><option value="inactive">Inactivos</option></select></div>
      <button className="button button--secondary" type="button" onClick={clearFilters} disabled={!search && !role && !status}>Limpiar filtros</button>
    </div>
    <p className="muted users-filter-summary" aria-live="polite">{matchingUsers.length} de {users.length} usuarios coinciden.</p>
    {matchingUsers.length === 0 ? <div className="empty-state"><p>No hay usuarios que coincidan con los filtros.</p><p className="muted">Probá otra búsqueda o limpiá los filtros para ver todas las personas.</p></div> : <>
    <table className="users-table"><caption>Usuarios de la vivienda · {users.length} en total</caption><thead><tr><th scope="col">Usuario</th><th scope="col">Rol</th><th scope="col">Estado</th>{canManage && <th scope="col">Acciones</th>}</tr></thead><tbody>
    {matchingUsers.slice(page * 10, page * 10 + 10).map((user) => <tr key={user.id}><th scope="row"><span>{user.username}</span><span className="user-email">{user.email}</span></th><td data-label="Rol">{roleLabels[user.role] ?? user.role}</td><td data-label="Estado"><span className={`status-badge status-badge--${user.is_active ? "online" : "offline"}`}>{user.is_active ? "Activo" : "Inactivo"}</span></td>{canManage && <td><div className="actions user-actions">
      <button className="button button--secondary" aria-label={`Cambiar rol de ${user.username}`} onClick={(event) => onAction("role", user, event.currentTarget)}><Shield aria-hidden="true" /><span>Rol</span></button>
      <button className="button button--secondary" aria-label={`${user.is_active ? "Deshabilitar" : "Habilitar"} ${user.username}`} onClick={(event) => onAction("active", user, event.currentTarget)}><UserRoundX aria-hidden="true" /><span>{user.is_active ? "Deshabilitar" : "Habilitar"}</span></button>
      <button className="button button--secondary" aria-label={`Restablecer contraseña de ${user.username}`} onClick={(event) => onAction("password", user, event.currentTarget)}><KeyRound aria-hidden="true" /><span>Contraseña</span></button>
    </div></td>}</tr>)}
  </tbody></table><nav className="pagination" aria-label="Paginación de usuarios"><button className="button button--secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Anterior</button><p aria-live="polite">Página {page + 1} de {pages}</p><button className="button button--secondary" disabled={page + 1 === pages} onClick={() => setPage(page + 1)}>Siguiente</button></nav></>}
  </div>;
}
