import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    "**/.next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  // ui-foundry 装进来的库资产（上游 beUI / Beautiful UI / 原创配方）：
  // 上游写法与 react-hooks v6 编译器级规则（refs / effect 内 setState 等）天然冲突，
  // 库代码经上游真实项目验证，这里整目录豁免这几条；我们自己的代码（app/、components/workbench 等）继续从严。
  {
    files: [
      "components/agents/**",
      "components/atoms/**",
      "components/motion/**",
      "components/primitives/**",
      "lib/hooks/use-favicon.ts",
      "lib/hooks/use-tap-gesture.ts",
      "lib/hooks/use-hover-gesture.ts",
      "lib/hooks/use-dismiss.ts",
      "lib/text-shimmer.ts",
      "lib/touch.ts",
    ],
    rules: {
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/globals": "off",
      "react-hooks/immutability": "off",
    },
  },
]);

export default eslintConfig;
