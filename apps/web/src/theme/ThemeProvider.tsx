import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ThemeContext } from "./ThemeContext";
import {
  applyTheme,
  persistTheme,
  readStoredTheme,
  type ThemeId,
} from "./theme";

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(() => {
    const initial = readStoredTheme();
    // Apply before first paint so children reading tokens see the right theme.
    if (typeof document !== "undefined") {
      applyTheme(initial);
    }
    return initial;
  });

  // D26: apply theme on the same turn as setState (before child effects read tokens).
  // Parent passive useEffect alone is too late — ChartGrid would read the previous data-theme.
  const setTheme = useCallback((next: ThemeId) => {
    applyTheme(next);
    persistTheme(next);
    setThemeState(next);
  }, []);

  useEffect(() => {
    // Keep attribute in sync on mount / external changes.
    applyTheme(theme);
    persistTheme(theme);
  }, [theme]);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
