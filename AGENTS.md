# FastAPI Backend — AI Instructions

## Project Context

This repository contains the FastAPI backend for Least Count Rush.

Shared project documentation is maintained in `../ai-knowledge/docs/`.

Read `GAME_OVERVIEW.md` and `SYSTEM_ARCHITECTURE.md` when relevant to the task.

## Architecture

- Follow the existing FastAPI route, service, schema, model, and utility patterns.
- Inspect related implementations before creating new modules.
- Keep HTTP route handlers focused on request handling and delegate business logic according to existing conventions.
- Reuse existing database, authentication, Redis, and logging utilities.
- Avoid unrelated refactoring and unnecessary dependencies.

## Game Rules and State

- Inspect `app/utils/game_rules.py` and the relevant game services before changing gameplay logic.
- Preserve existing rules unless the requirement explicitly changes them.
- Validate game actions on the backend where appropriate.
- Inspect concurrency handling, transactions, and locking before modifying shared game state.
- Do not assume in-memory state is safe across multiple server instances.

## REST API

Before modifying an endpoint, inspect:
- Its route and HTTP method.
- Pydantic request and response schemas.
- Authentication and authorization dependencies.
- Related service and persistence operations.
- Existing frontend callers and error handling.

Avoid breaking existing contracts without identifying the required frontend changes.

## WebSockets

Before modifying real-time behavior, inspect:
- Connection establishment and authentication.
- Message formats and validation.
- Room membership and connection management.
- State updates and broadcasts.
- Disconnect, reconnect, timeout, and cleanup behavior.
- Multi-instance coordination where applicable.

Do not invent event names or payload formats.

## Database and Redis

- Inspect existing SQLAlchemy models and database configuration before changing persistence.
- Follow existing migration conventions when changing database schemas.
- Preserve transaction integrity and appropriate async behavior.
- Inspect Redis key usage, expiry, locking, and cleanup before modifying Redis-dependent features.
- Never run destructive database operations without explicit authorization.

## Security

- Validate external input.
- Enforce authentication and authorization at the appropriate boundaries.
- Never expose secrets, tokens, or sensitive production data.
- Do not weaken security checks to make a test pass.

## Testing

- Follow existing pytest patterns.
- Add or update tests for changed business rules, endpoints, and WebSocket behavior where practical.
- Run relevant tests after modifications.
- Report tests that were not run or did not pass.

## Documentation

- Update shared documentation when verified architecture or API contracts change.
- Keep API examples consistent with actual schemas and implementation.
- Distinguish confirmed behavior from assumptions.

## Completion Report

Summarize changed files, behavior, API or schema changes, tests performed, frontend dependencies, and unresolved risks.