import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AgentForm } from "@/components/agent/agent-form";
import { agentSchema } from "@/lib/schemas/agent";

const savedAgent = {
  name: "Portero",
  system_prompt: "Recibí a visitantes.",
  language: "es-AR",
  voice: "default",
  voice_speed: 1,
  realtime_model: "env-default",
  openai_session_options: {},
  enabled: false,
  updated_at: null,
};

describe("AgentForm", () => {
  it("restores the last saved values and updates that baseline after saving", async () => {
    const user = userEvent.setup();
    render(
      <AgentForm
        initialValue={savedAgent}
        onSave={async (value) => ({ ...value, updated_at: "2026-09-05T12:00:00Z" })}
      />
    );
    const name = screen.getByLabelText(/nombre del agente/i);
    await user.clear(name);
    await user.type(name, "Cambio");
    await user.click(screen.getByRole("button", { name: /restaurar/i }));
    expect(name).toHaveValue("Portero");
    await user.clear(name);
    await user.type(name, "Recepción");
    await user.click(screen.getByRole("button", { name: /guardar/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(/guardada/i);
    await user.clear(name);
    await user.click(screen.getByRole("button", { name: /restaurar/i }));
    expect(name).toHaveValue("Recepción");
  });

  it("announces inline validation and preserves edits on a rejected save", async () => {
    const user = userEvent.setup();
    render(
      <AgentForm initialValue={savedAgent} onSave={async () => {
        throw new Error("offline");
      }} />
    );
    const name = screen.getByLabelText(/nombre del agente/i);
    await user.clear(name);
    await user.click(screen.getByRole("button", { name: /guardar/i }));
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent(/nombre/i);
    await user.type(name, "Recepción");
    await user.click(screen.getByRole("button", { name: /guardar/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no pudimos guardar/i);
    expect(name).toHaveValue("Recepción");
  });

  it("does not expose save or restore without edit permission", () => {
    render(
      <AgentForm
        initialValue={savedAgent}
        onSave={async (value) => ({ ...value, updated_at: null })}
        canEdit={false}
      />
    );
    expect(screen.getByLabelText(/nombre del agente/i)).toBeDisabled();
    expect(screen.queryByRole("button", { name: /guardar|restaurar/i })).not.toBeInTheDocument();
  });

  it("keeps conditional response timing and reasoning controls visible with guidance", () => {
    render(
      <AgentForm
        initialValue={savedAgent}
        onSave={async (value) => ({ ...value, updated_at: null })}
      />
    );

    expect(screen.getByLabelText("Tiempo de respuesta")).toBeDisabled();
    expect(screen.getByText(/requiere conversación natural/i)).toBeVisible();
    expect(screen.getByLabelText("Esfuerzo de razonamiento")).toBeDisabled();
    expect(screen.getByText(/requiere un modelo realtime 2/i)).toBeVisible();
  });

  it("exposes the nine friendly controls and submits their OpenAI session options", async () => {
    const user = userEvent.setup();
    let submitted: Record<string, unknown> | undefined;
    render(
      <AgentForm
        initialValue={{
          ...savedAgent,
          realtime_model: "gpt-realtime-2.1",
          openai_session_options: {
            custom_setting: "keep-me",
            audio: { input: { transcription: { keywords: ["Portero"] } } },
          },
        }}
        onSave={async (value) => {
          submitted = value;
          return { ...value, updated_at: null };
        }}
      />
    );

    expect(screen.queryByLabelText(/JSON/i)).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Modelo Realtime"), "gpt-realtime-2.1");
    await user.selectOptions(screen.getByLabelText("Tipo de voz"), "cedar");
    await user.clear(screen.getByLabelText(/Velocidad de voz/i));
    await user.type(screen.getByLabelText(/Velocidad de voz/i), "1.25");
    await user.selectOptions(screen.getByLabelText("Entorno del micrófono"), "far_field");
    await user.selectOptions(screen.getByLabelText("Detección de turnos"), "semantic_vad");
    await user.selectOptions(screen.getByLabelText("Tiempo de respuesta"), "high");
    await user.click(screen.getByLabelText("Permitir interrupciones"));
    await user.selectOptions(screen.getByLabelText("Longitud de respuesta"), "detailed");
    await user.selectOptions(screen.getByLabelText("Esfuerzo de razonamiento"), "low");
    await user.click(screen.getByRole("button", { name: /guardar/i }));

    expect(submitted).toMatchObject({
      voice: "cedar",
      voice_speed: 1.25,
      realtime_model: "gpt-realtime-2.1",
      openai_session_options: {
        custom_setting: "keep-me",
        audio: {
          input: {
            transcription: { keywords: ["Portero"] },
            noise_reduction: { type: "far_field" },
            turn_detection: {
              type: "semantic_vad",
              create_response: true,
              eagerness: "high",
              interrupt_response: false,
            },
          },
        },
        max_output_tokens: 600,
        reasoning: { effort: "low" },
      },
    });
  });

  it.each([
    ["name", ""],
    ["name", "x".repeat(129)],
    ["system_prompt", ""],
    ["system_prompt", "x".repeat(8001)],
    ["language", "e"],
    ["language", "x".repeat(33)],
    ["voice", ""],
    ["voice", "x".repeat(129)],
    ["voice_speed", 0.24],
    ["voice_speed", 1.51],
    ["realtime_model", ""],
    ["realtime_model", "x".repeat(129)],
  ])("rejects backend-invalid %s values", (field, value) => {
    expect(agentSchema.safeParse({ ...savedAgent, [field]: value }).success).toBe(false);
  });

  it("accepts the backend boundary values", () => {
    expect(
      agentSchema.safeParse({
        ...savedAgent,
        name: "x".repeat(128),
        system_prompt: "x".repeat(8000),
        language: "es",
        voice_speed: 0.25,
      }).success
    ).toBe(true);
    expect(agentSchema.safeParse({ ...savedAgent, voice_speed: 1.5 }).success).toBe(true);
  });
});
