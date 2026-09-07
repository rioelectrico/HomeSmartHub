import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PanelLayout from "@/app/(panel)/layout";
import DashboardPage from "@/app/(panel)/page";
import { ApiError } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

vi.mock("@/lib/auth", () => ({
  getCurrentUser: vi.fn(),
  logout: vi.fn(),
}));

describe("PanelLayout", () => {
  beforeEach(() => {
    replace.mockReset();
    vi.mocked(getCurrentUser).mockReset();
  });

  it("renders protected children inside the authenticated application shell", async () => {
    vi.mocked(getCurrentUser).mockResolvedValue({
      id: "8ba24942-d605-4b9d-b5f7-66927de904d1",
      username: "Ana",
      email: "ana@example.com",
      homes: [{ id: "0f8fad5b-d9cb-469f-a165-70867728950e", name: "Casa", timezone: "America/Argentina/Buenos_Aires", role: "owner", permissions: [] }],
    });

    render(<PanelLayout><DashboardPage /></PanelLayout>);

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeVisible();
    expect(screen.getByText(/sin permiso para ver diagnósticos/i)).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Todo en orden" })).not.toBeInTheDocument();
    expect(screen.getByText("Hola, Ana")).toBeVisible();
    expect(replace).not.toHaveBeenCalled();
  });

  it("redirects unauthenticated sessions to login before rendering protected children", async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(new ApiError(401, "UNAUTHENTICATED"));

    render(<PanelLayout><DashboardPage /></PanelLayout>);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByRole("heading", { name: "Dashboard" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Todo en orden" })).not.toBeInTheDocument();
  });
});
