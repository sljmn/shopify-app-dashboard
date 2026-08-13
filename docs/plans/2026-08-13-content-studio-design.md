# Mantle Content Studio Design

**Date:** 2026-08-13

## Purpose

Mantle will provide one internal workflow for finding bottom-of-funnel marketing opportunities, producing useful SEO articles or YouTube scripts, creating matching Newcraft illustrations, preventing overlap with existing Newcraft content, and publishing the approved result to WordPress.

The feature is for Newcraft's own Shopify apps. It is not an autonomous content farm. An operator chooses the app, language, channel, idea, sources, image, and publication action. English is the default language; Dutch can be selected per content item. WordPress drafts, scheduled posts, immediate publication, and explicit updates of existing posts are supported.

## Product Principles

1. **Evidence before prose.** Generated claims must come from the selected app profile or captured sources.
2. **Intent before keywords.** Each item owns a BOFU intent and must be differentiated from existing Newcraft content.
3. **Stages instead of one-shot generation.** Idea, brief, outline, draft, review, media, and publication are separately persisted.
4. **One editorial policy everywhere.** Direct OpenRouter generation and `Copy agent brief` use the same versioned rules and sources.
5. **Human-controlled publication.** Publishing is supported, but never happens in a background campaign. Direct publication requires a preview and explicit confirmation.
6. **Reproducible assets.** Prompt version, model, sources, style profile, output, and publication payload are retained.

## User Workflow

### Content index

The new `Content` navigation item opens a dense table of content projects. Operators can search and filter by app, language, channel, stage, author, overlap risk, and WordPress status. The primary action creates a content item; secondary actions synchronize the sitemap and test WordPress.

### Create an item

The operator chooses:

- an owned app;
- language, defaulting to English;
- `SEO article` or `YouTube`;
- a supplied topic or generated BOFU opportunities.

Mantle first assembles an evidence pack from the app content profile, Shopify listing metadata, relevant Newcraft inventory, and explicitly selected external sources. Idea results include search intent, buyer stage, why the app is relevant, overlap risk, and proposed internal links.

### Content workspace

The workspace has a central staged editor and a supporting evidence rail. It exposes:

- brief, outline, draft, review, preview, and version history;
- selected sources and the exact supported product claims;
- similar Newcraft posts and the overlap decision;
- proposed and verified internal links;
- generation controls and `Copy agent brief`;
- media selection and WordPress publication state.

SEO articles produce Gutenberg-compatible HTML, a slug, excerpt, metadata, link plan, and image brief. YouTube projects produce title options, hook, chapters, full script, visual notes, CTA, description, and link destinations.

## Existing Content and Cannibalization

Mantle synchronizes `https://newcraft.dev/marketing-post-sitemap.xml` and reads each listed page into a local inventory. A record stores canonical URL, WordPress ID when available, title, slug, language, headings, summary, captured text hash, publication date, last modification date, and linked app.

Before a new idea can advance, Mantle compares its query and intent with the inventory. The result is one of:

- `clear`: a distinct article is reasonable;
- `differentiate`: the proposed angle must be made explicit;
- `update_existing`: extending an existing post is the correct action;
- `blocked`: the conflict is unresolved.

The first implementation combines deterministic title, slug, heading, and term similarity with a structured model judgment. The model must name the overlapping pages and explain the intent conflict. A blocked item cannot publish until the operator selects an existing post to update or records a differentiated angle.

## Internal Linking

Every SEO article must include:

- the app pillar page near the start;
- the Shopify App Store listing where commercially relevant;
- two to four verified, contextually relevant Newcraft pages;
- varied, descriptive anchor text.

Mantle validates that every internal URL resolves, rejects duplicate anchors, and reports missing required links. Generated links are stored independently from the article so the operator can inspect and replace them. Later backlink suggestions for old posts are outside the first increment.

## App Content Profiles

Each owned app has one content profile with:

- pillar and Shopify listing URLs;
- supported product facts and allowed claims;
- forbidden or outdated claims;
- audiences, jobs, objections, and commercial use cases;
- default language and supported languages;
- WordPress related-app ID;
- illustration style profile;
- optional source URLs and editorial notes.

These fields belong in Mantle's management UI and Postgres. Secrets never enter the profile.

## Generation and Editorial Quality

OpenRouter is called server-side through a small client with strict timeouts and schema validation. Text requests use strict JSON Schema output and require a provider/model endpoint that supports the requested parameters. Model aliases are configured through environment variables and recorded on every run. The pipeline is deliberately staged:

1. build an evidence pack;
2. propose BOFU opportunities;
3. create a selected brief;
4. create an outline;
5. write the content in sections;
6. run a separate editorial review;
7. apply an explicit revision;
8. validate the final artifact deterministically.

The editorial policy incorporates the useful behavior of the existing SEO, copywriting, and anti-slop skills without depending on local Codex files at runtime. It requires specificity, direct language, short readable sections, buyer-focused examples, and a clear connection to the real app. It rejects invented product behavior, unsupported superlatives, generic introductions, repeated conclusions, keyword stuffing, emoji, em dashes, fake quotations, and filler headings.

Deterministic checks cover required structure, excerpt length, forbidden punctuation and phrases, duplicate paragraphs, link requirements, unresolved placeholders, and unsupported URLs. The review model handles intent match, factual support, usefulness, repetition, and tonal quality. Failures are visible and block publication; they do not silently rewrite approved content.

## Illustration System

Illustration generation uses OpenRouter's dedicated Image API with a separately configured image model and versioned Newcraft style profiles rather than a generic prompt. A style profile stores:

- named visual direction;
- palette, texture, composition, subject, and exclusion rules;
- aspect ratios and output roles;
- content-addressed reference images in Backblaze;
- a versioned prompt template.

The initial profiles reflect existing Newcraft work: warm textured editorial scenes with deep green, ochre, rust, and cream; and cleaner product/listing illustrations with navy, pale blue, cream, and terracotta. Generated images contain no rendered headline text. YouTube title overlays are rendered separately with deterministic typography so text stays sharp.

Generated media is validated, stored content-addressed in the existing Backblaze bucket under a `content/` prefix, and only uploaded to WordPress when selected. Alt text remains editable. The selected featured image is explicit and versioned.

## WordPress Integration

Mantle implements a typed WordPress REST client based on the proven `scripts/wp-deploy.sh` contract:

- HTTP basic authentication with a WordPress Application Password;
- custom post type `marketing-post`;
- Gutenberg-compatible content;
- title, slug, excerpt, status, scheduled date, and ACF `related_apps`;
- media upload and `featured_media` assignment;
- create, update, draft, future, and publish states.

Credentials live only in Dokku environment variables. Management shows whether each required variable is present and provides a connection test without revealing values.

`content_publications` stores the WordPress post ID, URL, state, last payload hash, response, and error. Retrying an unchanged action returns the existing result. Updating an existing post requires the operator to choose it explicitly. Immediate publication shows a final confirmation containing app, language, title, URL, and status.

## Persistence

The schema consists of focused tables:

- `app_content_profiles`: app-owned product and editorial facts;
- `content_projects`: identity, channel, language, query, intent, stage, author, and overlap resolution;
- `content_versions`: immutable generated or edited stage output with model and policy version;
- `content_sources`: captured source metadata, digest, selected excerpts, and project membership;
- `content_inventory`: synchronized Newcraft pages and their search/intent representation;
- `content_links`: suggested and used links with validation state;
- `content_style_profiles`: versioned illustration instructions and reference objects;
- `content_media`: generated asset metadata, Backblaze key, alt text, selection, and WordPress media ID;
- `content_quality_checks`: named check, severity, result, and evidence;
- `content_runs`: operational log for imports, generations, reviews, images, and publications;
- `content_publications`: idempotent WordPress state.

Generated and edited content is never overwritten. The project points to the accepted version for each stage.

## Reliability and Security

- Model responses are accepted only after JSON schema validation.
- External fetches use timeouts, size limits, content-type checks, bounded redirects, and an allowlist for Newcraft and configured source hosts.
- Sitemap imports are transactional and retain the last successful inventory on failure.
- Publication requires a fresh inventory sync and passing required checks.
- WordPress and OpenRouter secrets are read from environment variables and never logged or rendered.
- OpenRouter requests exclude credentials, merchant PII, and unrelated dashboard data.
- Every external operation has a visible run record with state, duration, safe error, and retry relationship.
- WordPress creation and media upload use stable idempotency keys in Mantle even though WordPress does not supply them.

## Delivery Slices

1. App profiles, WordPress inventory, BOFU ideas, agent briefs, and overlap decisions.
2. Direct OpenRouter brief, outline, SEO article, and YouTube generation with immutable versions.
3. Internal-link and editorial quality gates.
4. Style profiles, generated media, Backblaze persistence, and media selection.
5. WordPress draft, schedule, publish, and explicit update flows.

Each slice is independently useful and must pass focused tests plus the full suite before deployment.
