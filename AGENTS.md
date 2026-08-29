# Code organization

## Separation of responsibilities

- Split code by responsibility and domain.
- Follow the Single Responsibility Principle: each module, class, or function
  should have one clear purpose.
- Do not mix business logic, data access, UI, API handling, and utilities in
  one file.

## File size

- Keep files concise and maintainable.
- Avoid files exceeding 600 lines of code. Extract components, services,
  utilities, or submodules before reaching that limit.

## Modular architecture

- Prefer small, reusable, composable modules over monoliths.
- Organize code in clear feature- or domain-based folders.
- Minimize coupling and maximize encapsulation.

## Maintainability

- Favor readability over cleverness.
- Keep functions focused and relatively small.
- Extract repeated logic into shared utilities or services.
- Make new features possible with minimal changes to existing code.

Build the system as a collection of small, independent, well-defined modules.
