import { THEME_LABELS, THEMES, type ThemeId } from "../theme/theme";
import { useTheme } from "../theme/useTheme";

export function ThemeSwitch() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="theme-switch" role="group" aria-label="主題切換">
      {THEMES.map((id: ThemeId) => (
        <button
          key={id}
          type="button"
          className={
            theme === id
              ? "theme-switch__btn theme-switch__btn--active"
              : "theme-switch__btn"
          }
          aria-pressed={theme === id}
          onClick={() => {
            setTheme(id);
          }}
        >
          {THEME_LABELS[id]}
        </button>
      ))}
    </div>
  );
}
