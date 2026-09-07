import { z } from "zod";

export const deviceSchema = z.object({
  device_id: z.string().regex(/^PI-[0-9]{6}$/, "Usá el formato PI-000000."),
  name: z.string().trim().min(1, "Ingresá un nombre.").max(128, "El nombre admite hasta 128 caracteres."),
});
