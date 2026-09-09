import type { Metadata } from "next";
import { PorteroSimulator } from "@/components/simulator/portero-simulator";

export const metadata: Metadata = {
  title: "Simulador de portero · Home Smart Hub",
  description: "Probá una visita por voz con el portero, manos libres.",
};

export default function SimulatorPage() {
  return <PorteroSimulator />;
}

