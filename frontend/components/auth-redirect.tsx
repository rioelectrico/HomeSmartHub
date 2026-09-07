"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export function AuthRedirect() {
  const router = useRouter();

  useEffect(() => {
    const redirect = () => router.replace("/login");
    window.addEventListener("portero:unauthorized", redirect);
    return () => window.removeEventListener("portero:unauthorized", redirect);
  }, [router]);

  return null;
}
