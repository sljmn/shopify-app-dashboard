# Review Collection Playbook Design

## Goal

Add a protected, in-app playbook that gives an implementation agent one
source of truth for connecting a Shopify app to Mantle contact capture and
native review prompts. Mantle must also exclude Shopify Partner development
stores before issuing a prompt.

## Product shape

- A dedicated **Review collection playbook** lives beside the existing New app
  runbook under Management.
- The playbook describes the complete trusted flow: Shopify authentication,
  contact capture, idempotent success events, Mantle eligibility, the native
  Reviews API call, outcome reporting, privacy, testing, and rollout.
- Book Importer is the concrete reference implementation, but the contract is
  language-neutral so an agent can apply it to any app.
- Secret values never appear. The playbook names only environment variables and
  authenticated endpoints.
- Development stores are excluded centrally when Shopify reports
  `shop.plan.partnerDevelopment = true`. Unknown legacy store types are not
  guessed and remain eligible until an app supplies the authoritative value.

## Navigation

The Integrations page links to both runbooks. The New app runbook and each app
edit page cross-link to the review playbook so a new integration cannot miss
the review-specific work.

## Verification

- The playbook route requires dashboard authentication.
- Route tests assert the agent contract, Book Importer reference, development
  store rule, and navigation links.
- Review policy tests prove an explicitly marked development store cannot
  receive a decision.
- Book Importer tests prove the Shopify GraphQL plan flag is captured and sent
  to Mantle.
