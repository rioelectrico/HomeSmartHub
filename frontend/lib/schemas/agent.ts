import { z } from "zod";

const openAIOptionsSchema = z.record(z.string(), z.unknown());

export const agentSchema = z.object({
  name: z.string().min(1, "Ingresá el nombre del agente.").max(128, "El nombre admite hasta 128 caracteres."),
  system_prompt: z.string().min(1, "Ingresá las instrucciones.").max(8000, "Las instrucciones admiten hasta 8000 caracteres."),
  language: z.string().min(2, "Ingresá al menos 2 caracteres.").max(32, "El idioma admite hasta 32 caracteres."),
  voice: z.string().min(1, "Ingresá una voz.").max(128, "La voz admite hasta 128 caracteres."),
  voice_speed: z
    .number({ error: "Ingresá una velocidad válida." })
    .min(0.25, "La velocidad mínima es 0,25.")
    .max(1.5, "La velocidad máxima es 1,5."),
  realtime_model: z.string().min(1, "Ingresá un modelo.").max(128, "El modelo admite hasta 128 caracteres."),
  openai_session_options: openAIOptionsSchema,
  enabled: z.boolean(),
});

export type AgentValues = z.infer<typeof agentSchema>;
