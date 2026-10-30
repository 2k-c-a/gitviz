import js from '@eslint/js';
import tseslint from 'typescript-eslint';
export default [
  { ignores: ['dist/**', 'out/**', 'node_modules/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  { files: ['**/*.ts'], rules: { '@typescript-eslint/no-explicit-any': 'error' } },
  { files: ['scripts/*.mjs', '*.mjs'], languageOptions: { globals: { Buffer: 'readonly', URL: 'readonly', process: 'readonly' } } },
  { files: ['ui/*.js'], languageOptions: { globals: { document: 'readonly', window: 'readonly' } } },
];
