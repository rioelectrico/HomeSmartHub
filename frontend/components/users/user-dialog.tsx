"use client";
import * as Dialog from "@radix-ui/react-dialog";
import { useRef, useState } from "react";
import { FormError } from "@/components/form-error";
import type { UserAction } from "@/components/users/users-table";
import { apiFetch, ApiError } from "@/lib/api";
import { passwordResetSchema, roleLabels, userCreateSchema, userRoleSchema } from "@/lib/schemas/user";
import type { HomeUser } from "@/types/api";

export type UserDialogSelection = { action: UserAction; user?: HomeUser; trigger: HTMLButtonElement };
export function UserDialog({ homeId, selection, onClose, onSaved }: { homeId: string; selection: UserDialogSelection; onClose: () => void; onSaved: (message: string) => void }) {
  const { action, user, trigger } = selection;
  const [values, setValues] = useState({ username: "", email: "", password: "", role: user?.role ?? "read_only" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSubmitting, setSubmitting] = useState(false);
  const busy = useRef(false);
  const title = action === "create" ? "Crear usuario" : action === "role" ? "Cambiar rol" : action === "password" ? "Restablecer contraseña" : user?.is_active ? "Deshabilitar usuario" : "Habilitar usuario";
  const submitLabel = action === "create" ? "Crear usuario" : action === "role" ? "Guardar rol" : action === "password" ? "Confirmar nueva contraseña" : user?.is_active ? "Confirmar deshabilitación" : "Confirmar habilitación";

  function close() { if (!busy.current) { setValues({ username: "", email: "", password: "", role: "read_only" }); onClose(); } }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy.current) return;
    const schema = action === "create" ? userCreateSchema : action === "password" ? passwordResetSchema : userRoleSchema;
    const parsed = schema.safeParse(values);
    if (action !== "active" && !parsed.success) {
      const nextErrors: Record<string, string> = {};
      for (const issue of parsed.error.issues) nextErrors[String(issue.path[0])] ??= issue.message;
      setErrors(nextErrors);
      document.getElementById(`user-${Object.keys(nextErrors)[0]}`)?.focus();
      return;
    }
    setErrors({}); busy.current = true; setSubmitting(true);
    try {
      const base = `/api/homes/${homeId}/users`;
      await apiFetch<HomeUser | void>(action === "create" ? base : `${base}/${user!.id}${action === "password" ? "/reset-password" : ""}`, {
        method: action === "create" || action === "password" ? "POST" : "PATCH",
        body: JSON.stringify(action === "active" ? { is_active: !user!.is_active } : parsed.data),
      });
      setValues({ username: "", email: "", password: "", role: "read_only" });
      onSaved(action === "password" ? "Contraseña actualizada. Se cerraron las sesiones del usuario." : action === "create" ? "Usuario creado." : "Usuario actualizado.");
    } catch (error) {
      setErrors({ root: error instanceof ApiError && error.status === 403 ? "No tenés permiso para realizar esta acción." : error instanceof ApiError && error.status === 409 ? "Ese usuario o email ya existe." : "No pudimos guardar el cambio. Intentá nuevamente." });
    } finally { busy.current = false; setSubmitting(false); }
  }

  const field = (name: "username" | "email" | "password", label: string, type = "text", maxLength = 64) => <div className="field"><label htmlFor={`user-${name}`}>{label}</label><input id={`user-${name}`} type={type} maxLength={maxLength} autoComplete={name === "password" ? "new-password" : name === "email" ? "email" : "off"} value={values[name]} onChange={(event) => setValues({ ...values, [name]: event.target.value })} aria-invalid={Boolean(errors[name])} aria-describedby={errors[name] ? `user-${name}-error` : name === "password" ? "password-hint" : undefined} /><FormError id={`user-${name}-error`} message={errors[name]} /></div>;

  return <Dialog.Root open onOpenChange={(open) => { if (!open) close(); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content" onCloseAutoFocus={(event) => { event.preventDefault(); trigger.focus(); }} onEscapeKeyDown={(event) => { if (busy.current) event.preventDefault(); }} onPointerDownOutside={(event) => { if (busy.current) event.preventDefault(); }}>
    <Dialog.Title>{title}</Dialog.Title><Dialog.Description>{action === "create" ? "Creá una cuenta y asignale un rol en esta vivienda." : action === "password" ? `Se cambiará la contraseña de ${user?.username} y se cerrarán todas sus sesiones.` : action === "active" ? `El cambio de estado de ${user?.username} afecta su cuenta en todos sus hogares.` : `Confirmá el nuevo rol de ${user?.username} en esta vivienda.`}</Dialog.Description>
    <form noValidate onSubmit={submit}><FormError id="user-action-error" message={errors.root} /><fieldset disabled={isSubmitting}>
      {action === "create" && <>{field("username", "Nombre de usuario")}{field("email", "Email", "email", 320)}</>}
      {(action === "create" || action === "password") && <>{field("password", "Contraseña temporal", "password", 1024)}<p id="password-hint" className="muted">Usá al menos 12 caracteres. La contraseña se borra de este formulario al cerrarlo.</p></>}
      {(action === "create" || action === "role") && <div className="field"><label htmlFor="user-role">Rol</label><select id="user-role" value={values.role} onChange={(event) => setValues({ ...values, role: event.target.value })} aria-invalid={Boolean(errors.role)} aria-describedby={errors.role ? "user-role-error" : undefined}>{!roleLabels[values.role] && <option value={values.role}>{values.role}</option>}{Object.entries(roleLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><FormError id="user-role-error" message={errors.role} /></div>}
    </fieldset><div className="actions"><button className="button button--primary" disabled={isSubmitting} type="submit">{isSubmitting ? "Guardando…" : submitLabel}</button><button className="button button--secondary" disabled={isSubmitting} type="button" onClick={close}>Cancelar</button></div></form>
  </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
