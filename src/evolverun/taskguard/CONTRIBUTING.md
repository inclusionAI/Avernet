# Contributing

Thank you for your interest in contributing to taskguard! This document outlines the guidelines for contributing.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/<your-username>/taskguard.git`
3. Install dependencies: `npm install`
4. Build: `npm run build`
5. Run tests: `npm test`

## Development Workflow

1. Create a feature branch: `git checkout -b feature/your-feature-name`
2. Make your changes, keeping commits focused and well-described
3. Ensure tests pass: `npm test`
4. Ensure the build succeeds: `npm run build`
5. Push to your fork and open a Pull Request

## Code Style

- TypeScript with strict type checking enabled
- Use `import type` for type-only imports
- Follow existing naming conventions (camelCase for variables, PascalCase for types/interfaces)
- Add JSDoc comments for public API functions and types
- Avoid `any` — use `unknown` with proper type narrowing

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):
- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation changes
- `refactor:` code restructuring without behavior change
- `test:` adding or fixing tests
- `chore:` build/tooling changes

## Pull Requests

- Keep PRs focused — one feature or fix per PR
- Include a clear description of what changed and why
- Update tests and documentation as needed
- Ensure CI passes before requesting review

## Reporting Issues

- Use GitHub Issues to report bugs or request features
- Include a minimal reproduction for bug reports
- Specify your Node.js version and OS

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
