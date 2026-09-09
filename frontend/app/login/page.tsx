"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { FormError } from "@/components/form-error";
import { ApiError } from "@/lib/api";
import { login } from "@/lib/auth";
import { loginSchema, type LoginValues } from "@/lib/schemas/auth";
import type { CurrentUser } from "@/types/api";

export default function LoginPage() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const errorSummaryRef = useRef<HTMLDivElement>(null);
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });

  useEffect(() => {
    if (errors.root?.message) errorSummaryRef.current?.focus();
  }, [errors.root?.message]);

  async function onSubmit(values: LoginValues) {
    try {
      setUser(await login(values));
    } catch (error) {
      const message = error instanceof ApiError && error.code === "INVALID_CREDENTIALS"
        ? "Usuario o contraseña incorrectos."
        : "No pudimos iniciar sesión. Revisá tu conexión e intentá de nuevo.";
      setError("root", { message });
    }
  }

  if (user) {
    return (
      <main className="auth-page">
        <section className="auth-card auth-card--success" aria-labelledby="login-success-title">
          <ShieldCheck className="success-icon" aria-hidden="true" />
          <p className="eyebrow">Sesión protegida</p>
          <h1 id="login-success-title">Dashboard listo</h1>
          <p>Bienvenido, {user.username}. Tu sesión se administra mediante una cookie segura.</p>
          <Link className="button button--primary" href="/">Ir al panel</Link>
        </section>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-card__mark" aria-hidden="true"><KeyRound /></div>
        <p className="eyebrow">Home Smart Hub</p>
        <h1 id="login-title">Ingresá a tu hogar</h1>
        <p className="auth-card__intro">Gestioná los accesos desde un único lugar seguro.</p>
        <form noValidate onSubmit={handleSubmit(onSubmit)}>
          <div ref={errorSummaryRef} tabIndex={-1} className="error-summary">
            <FormError id="login-error" message={errors.root?.message} />
          </div>
          <div className="field">
            <label htmlFor="identifier">Usuario o email</label>
            <input id="identifier" autoComplete="username" aria-describedby={errors.identifier ? "identifier-error" : undefined} aria-invalid={Boolean(errors.identifier)} {...register("identifier")} />
            <FormError id="identifier-error" message={errors.identifier?.message} />
          </div>
          <div className="field">
            <label htmlFor="password">Contraseña</label>
            <input id="password" type="password" autoComplete="current-password" aria-describedby={errors.password ? "password-error" : undefined} aria-invalid={Boolean(errors.password)} {...register("password")} />
            <FormError id="password-error" message={errors.password?.message} />
          </div>
          <button className="button button--primary button--full" type="submit" disabled={isSubmitting}>
            {isSubmitting && <LoaderCircle className="spin" aria-hidden="true" />}
            {isSubmitting ? "Ingresando…" : "Iniciar sesión"}
          </button>
        </form>
      </section>
    </main>
  );
}
