import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "@/app/login/page";

describe("LoginPage", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/auth/login")) {
          return new Response(null, { status: 204 });
        }
        if (url.endsWith("/api/auth/me")) {
          return Response.json({
            id: "8ba24942-d605-4b9d-b5f7-66927de904d1",
            username: "admin",
            email: "admin@example.com",
            homes: [],
          });
        }
        return new Response(null, { status: 404 });
      }),
    );
  });

  it("submits credentials without storing a token", async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByLabelText(/usuario o email/i), "admin");
    await user.type(screen.getByLabelText(/contraseña/i), "ValidPass!42");
    await user.click(screen.getByRole("button", { name: /iniciar sesión/i }));

    expect(await screen.findByText(/dashboard/i)).toBeVisible();
    expect(localStorage.getItem("token")).toBeNull();
  });
});
