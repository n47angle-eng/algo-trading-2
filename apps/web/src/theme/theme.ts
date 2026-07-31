export const THEMES = ["dark", "light", "nature"] as const;

export type ThemeId = (typeof THEMES)[number];

export const THEME_LABELS: Record<ThemeId, string> = {
  dark: "深色",
  light: "淺色",
  nature: "自然",
};

export const THEME_STORAGE_KEY = "futures-research.theme";

export function isThemeId(value: string | null | undefined): value is ThemeId {
  return value === "dark" || value === "light" || value === "nature";
}

export function readStoredTheme(): ThemeId {
  if (typeof window === "undefined") {
    return "dark";
  }
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (isThemeId(raw)) {
      return raw;
    }
  } catch {
    /* private mode / denied storage */
  }
  return "dark";
}

export function applyTheme(theme: ThemeId): void {
  document.documentElement.setAttribute("data-theme", theme);
  const metas = document.querySelectorAll('meta[name="theme-color"]');
  const bg =
    getComputedStyle(document.documentElement)
      .getPropertyValue("--color-bg")
      .trim() || "#050505";
  metas.forEach((meta) => {
    meta.setAttribute("content", bg);
  });
}

export function persistTheme(theme: ThemeId): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* ignore */
  }
}
