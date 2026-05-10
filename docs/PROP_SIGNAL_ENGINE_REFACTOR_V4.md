# PROP SIGNAL ENGINE REFACTOR V4

## Main Goal

Convert the platform from overfiltered AI-driven architecture into a deterministic institutional-style setup engine.

## Core Principles

- AI does NOT create trades.
- Deterministic setup engine becomes primary.
- SMC / Options / Volume become weighted scoring layers.
- Trade flow must remain active.
- Minimum target: 3-4 setups per pair/day.

## Mandatory Setup Engine

Required:
- Liquidity sweep
- Displacement
- MSS
- FVG reclaim

Everything else becomes scoring.

## New Setup Types

1. Liquidity Sweep Reversal
2. London Continuation
3. News Liquidity Trap

## Scoring Model

Base score: 50

Additive modifiers:
- SMC alignment
- HTF bias
- options support
- futures confirmation
- volume confirmation
- AI context adjustment

Negative modifiers:
- high impact news
- counter-trend structure
- low session quality

## AI Refactor

AI responsibilities:
- context
- volatility regime
- session quality
- narrative generation
- confidence adjustment

AI must NOT:
- invent entries
- invent SL/TP
- block entire signal pipeline

## Signal Grades

A+ = 85+
A = 75+
B = 65+
Reject <65

## Risk Model

A+ = 1%
A = 0.75%
B = 0.35%

## Required New Modules

core/setups/
core/scoring/
core/audit/
core/execution/

## Critical Requirement

All rejected trades must explain why.

No silent failures.

## Stability Goals

- MT5 reconnect protection
- AI timeout fallback
- cached news/options
- retry protection
- execution audit logs

## Final Objective

The platform should operate statistically like a prop-desk engine:
- repeatable setups
- continuous signal flow
- weighted confluence
- controlled risk
- institutional dashboard logic
