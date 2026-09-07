import { z } from "zod";
const password = z.string().min(12, "La contraseña necesita al menos 12 caracteres.").max(1024, "La contraseña admite hasta 1024 caracteres.");
const role = z.string().min(1, "Seleccioná un rol.").max(64, "El rol admite hasta 64 caracteres.");
export const userCreateSchema = z.object({
  username: z.string().min(3, "El nombre necesita al menos 3 caracteres.").max(64, "El nombre admite hasta 64 caracteres."),
  email: z.string().min(3, "Ingresá un email válido.").max(320, "El email admite hasta 320 caracteres.").refine((value) => {
    const separator = value.indexOf("@");
    return separator > 0 && value.slice(separator + 1).includes(".");
  }, "Ingresá un email válido.").transform((value) => value.toLowerCase()),
  password,
  role,
});
export const passwordResetSchema = z.object({ password });
export const userRoleSchema = z.object({ role });
export type UserCreateValues = z.infer<typeof userCreateSchema>;

// Matches the roles seeded by backend/app/bootstrap.py. The server validates assignments.
export const roleLabels: Record<string, string> = { administrator: "Administrador", owner: "Propietario", operator: "Operador", user: "Usuario", read_only: "Solo lectura" };
