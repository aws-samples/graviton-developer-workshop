#!/bin/bash
# End-to-end evaluation script for Graviton Migration Power
#
# This script:
# 1. Uses the existing MIGRATION_SUMMARY.md as a baseline "good" output
# 2. Runs the LLM judge against it
# 3. Reports scores
#
# Usage:
#   ./run_eval.sh                    # Uses OpenAI (requires OPENAI_API_KEY)
#   ./run_eval.sh bedrock            # Uses AWS Bedrock
#   ./run_eval.sh openai gpt-4o     # Specify model

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
PROVIDER="${1:-openai}"
MODEL="${2:-}"

echo "============================================"
echo "  Graviton Migration Power - Evaluation"
echo "============================================"
echo ""
echo "Provider: $PROVIDER"
[ -n "$MODEL" ] && echo "Model: $MODEL"
echo ""

# Create results directory
mkdir -p "$RESULTS_DIR"

# Copy ground truth outputs as baseline (the known-good results)
echo "Setting up baseline results from sample projects..."
echo ""

if [ -f "$SCRIPT_DIR/../samples/graviton-migration-test/MIGRATION_SUMMARY.md" ]; then
    cp "$SCRIPT_DIR/../samples/graviton-migration-test/MIGRATION_SUMMARY.md" \
       "$RESULTS_DIR/graviton-migration-test.md"
    echo "  ✓ graviton-migration-test.md (from MIGRATION_SUMMARY.md)"
fi

echo ""
echo "Running LLM judge..."
echo ""

# Build command
CMD="python3 $SCRIPT_DIR/eval_judge.py --all --provider $PROVIDER --results-dir results"
[ -n "$MODEL" ] && CMD="$CMD --model $MODEL"
CMD="$CMD --save $RESULTS_DIR/eval_results.json"

# Run evaluation
cd "$SCRIPT_DIR"
eval $CMD

echo ""
echo "Results saved to: $RESULTS_DIR/eval_results.json"
echo "Done!"
