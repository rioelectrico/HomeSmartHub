"use client";
import { createContext, useContext } from "react";
import type { HomeAccess } from "@/types/api";
export const HomeContext = createContext<HomeAccess | null>(null);
export const useHome = () => useContext(HomeContext);
