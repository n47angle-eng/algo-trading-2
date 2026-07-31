/** @type {import('stylelint').Config} */
export default {
  extends: ["stylelint-config-standard"],
  plugins: ["stylelint-declaration-strict-value"],
  rules: {
    // D16: component CSS may only use design tokens (no raw colors).
    "color-no-hex": true,
    "function-disallowed-list": ["rgb", "rgba", "hsl", "hsla", "hwb", "lab", "lch", "oklab", "oklch"],
    "scale-unlimited/declaration-strict-value": [
      [
        "/color$/",
        "fill",
        "stroke",
        "background",
        "background-color",
        "border-color",
        "outline-color",
        "caret-color",
        "box-shadow",
        "text-decoration-color",
      ],
      {
        ignoreValues: [
          "currentColor",
          "transparent",
          "inherit",
          "none",
          "0",
          "/^var\\(/",
          "/^color-mix\\(/",
          "/^linear-gradient\\(/",
          "/^radial-gradient\\(/",
        ],
        disableFix: true,
      },
    ],
    "selector-class-pattern": null,
    "custom-property-pattern": null,
    "import-notation": null,
    "media-feature-range-notation": null,
    // Glass fallback requires -webkit- prefix in Safari PWA.
    "property-no-vendor-prefix": null,
    "declaration-block-no-redundant-longhand-properties": null,
  },
  overrides: [
    {
      // tokens.css is the only file allowed to hold raw palette values.
      files: ["**/tokens.css"],
      rules: {
        "color-no-hex": null,
        "function-disallowed-list": null,
        "scale-unlimited/declaration-strict-value": null,
        "alpha-value-notation": null,
        "color-function-notation": null,
        "hue-degree-notation": null,
        "color-hex-length": null,
        "custom-property-empty-line-before": null,
      },
    },
  ],
};
