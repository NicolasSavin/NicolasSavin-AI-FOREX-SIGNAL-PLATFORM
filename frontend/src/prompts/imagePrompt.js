export const buildIdeaImagePrompt = (idea) => `
Create a realistic forex trading chart for ${idea.symbol}.

Show:
- clear candlestick chart
- order blocks
- imbalance (FVG)
- liquidity zones
- support/resistance
- arrows showing direction ${idea.direction}
- entry, stop loss, take profit

Style: professional trading terminal
NOT abstract art
`;
