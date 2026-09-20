const js = require("@eslint/js");

module.exports = [
  // Vendored/generated/tooling-only JS - never our own source, so never
  // linted. `**/*.js` below is deliberately repo-wide (not scoped to
  // static/js/) so a script added anywhere else later isn't silently
  // skipped, which makes excluding vendored code (.venv's own bundled
  // JS, e.g. Django admin's i18n_catalog.js) load-bearing rather than
  // just tidy - without it, `eslint .` parses third-party bundles too.
  {
    ignores: [
      "node_modules/**",
      ".venv/**",
      "venv/**",
      "staticfiles/**",
      "eslint.config.js",
    ],
  },
  {
    files: ["**/*.js"],
    ...js.configs.recommended,
  },
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: {
        document: "readonly",
        window: "readonly",
        fetch: "readonly",
        console: "readonly",
        AbortController: "readonly",
        TomSelect: "readonly",
      },
    },
    rules: {
      // These files are loaded via plain <script src> (no bundler), and
      // their top-level functions (initPersonPicker, initHebrewAutofill)
      // are entry points called from an inline <script> block in the
      // template, not from anything eslint can see - "vars: local"
      // limits no-unused-vars to variables in a *nested* scope, so a
      // genuinely-unused local still gets caught.
      "no-unused-vars": ["error", { vars: "local" }],
    },
  },
];
