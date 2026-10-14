import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library unmounts automatically only with vitest `globals: true`; without it rendered trees would
// accumulate across the tests of a file.
afterEach(() => cleanup());
