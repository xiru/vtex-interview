import js from '@eslint/js';
import nodePlugin from 'eslint-plugin-n';
import globals from 'globals';

export default [
  { ignores: ['node_modules/', 'coverage/'] },
  js.configs.recommended,
  nodePlugin.configs['flat/recommended-module'],
  {
    languageOptions: { ecmaVersion: 2024, sourceType: 'module', globals: globals.node },
  },
  {
    files: ['src/**/*.js'],
    ignores: ['src/api.js'],
    rules: {
      // Read-only guarantee: only the API client may perform HTTP requests.
      'no-restricted-globals': ['error', { name: 'fetch', message: 'Use src/api.js (GET only).' }],
      // EJS hardening: never pass request objects straight into a template.
      'no-restricted-syntax': [
        'error',
        {
          selector: "CallExpression[callee.property.name='render'] > MemberExpression[object.name='req']",
          message: 'Build an explicit locals object instead of passing req.* to res.render.',
        },
      ],
    },
  },
];
