import { z } from "zod";

export const loginSchema = z.object({
  identifier: z.string().trim().min(1, "Ingresá tu usuario o email."),
  password: z.string().min(1, "Ingresá tu contraseña."),
});

export type LoginValues = z.infer<typeof loginSchema>;
