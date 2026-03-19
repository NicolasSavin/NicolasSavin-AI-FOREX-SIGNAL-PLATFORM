export const buildIdeaImagePrompt = (idea) => `
Create a realistic forex trading chart for ${idea.symbol}.
Show a clear candlestick chart on a dark professional trading terminal.

Required elements:
- obvious candlestick structure
- order blocks
- imbalance / FVG zones
- support and resistance levels
- liquidity zones
- arrows showing expected direction: ${idea.direction}
- entry area
- stop loss / invalidation
- take profit targets
- short analytical labels that explain the logic

The image must look like a real trading analysis chart, not abstract finance art.
`;
