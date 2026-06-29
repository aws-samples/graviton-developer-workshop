"""
LLM-as-a-Judge Evaluation for Graviton Migration Power

Scores migration reports against expected findings using an LLM judge.

Usage:
    # Score a single output
    python eval_judge.py --output migration_output.md --test-case graviton-migration-test

    # Score all outputs in results/ directory
    python eval_judge.py --all

    # Use AWS Bedrock instead of OpenAI
    python eval_judge.py --all --provider bedrock --model anthropic.claude-sonnet-4-20250514-v1:0
"""

import argparse
import json
import os
import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class EvalScore:
    """Score for a single evaluation dimension."""
    dimension: str
    score: int  # 1-5
    reasoning: str


@dataclass
class EvalResult:
    """Complete evaluation result for one test case."""
    test_case: str
    scores: list[EvalScore]
    total_score: int
    max_score: int
    percentage: float
    findings_detected: list[str]
    findings_missed: list[str]
    false_positives: list[str]
    judge_summary: str


JUDGE_SYSTEM_PROMPT = """You are an expert evaluator for ARM64/Graviton migration analysis tools.

You will be given:
1. A test case description with EXPECTED findings (ground truth)
2. The ACTUAL output produced by a migration analysis tool

Your job is to score the output on 5 dimensions (1-5 scale each):

## Scoring Rubric

### 1. Completeness (1-5)
- 5: All expected findings identified, no gaps
- 4: Most findings identified (>80%), minor gaps
- 3: Core findings identified (~60%), some gaps
- 2: Only obvious findings (~40%), significant gaps
- 1: Major findings missed (<40%)

### 2. Accuracy (1-5)
- 5: All findings are factually correct, no hallucinations
- 4: Minor inaccuracies that don't affect migration outcome
- 3: Some inaccuracies but core recommendations are sound
- 2: Multiple inaccuracies that could mislead
- 1: Major factual errors or hallucinations

### 3. Actionability (1-5)
- 5: Every finding has specific, copy-paste-ready fix with context
- 4: Most findings have clear fixes, minor vagueness
- 3: Fixes provided but some lack specifics
- 2: Generic recommendations, user needs to research fixes
- 1: Findings listed without actionable guidance

### 4. Structure (1-5)
- 5: Well-organized, clear sections, easy to follow, prioritized
- 4: Good organization, minor formatting issues
- 3: Readable but could be better organized
- 2: Hard to follow, poor organization
- 1: Unstructured dump of information

### 5. Safety (1-5)
- 5: Clearly warns about breaking changes, suggests testing, asks before modifying
- 4: Mentions testing/verification, mostly safe recommendations
- 3: Recommendations are safe but doesn't emphasize verification
- 2: Some recommendations could break things without warning
- 1: Suggests changes that could break production without warning

## Response Format

Respond with ONLY valid JSON in this exact format:
{
  "scores": [
    {"dimension": "completeness", "score": <1-5>, "reasoning": "<brief explanation>"},
    {"dimension": "accuracy", "score": <1-5>, "reasoning": "<brief explanation>"},
    {"dimension": "actionability", "score": <1-5>, "reasoning": "<brief explanation>"},
    {"dimension": "structure", "score": <1-5>, "reasoning": "<brief explanation>"},
    {"dimension": "safety", "score": <1-5>, "reasoning": "<brief explanation>"}
  ],
  "findings_detected": ["<list of expected findings that were correctly identified>"],
  "findings_missed": ["<list of expected findings that were NOT identified>"],
  "false_positives": ["<list of findings reported that are NOT in expected findings and are incorrect>"],
  "summary": "<2-3 sentence overall assessment>"
}
"""


def build_judge_prompt(test_case: dict, actual_output: str) -> str:
    """Build the evaluation prompt for the LLM judge."""
    expected = json.dumps(test_case["expected_findings"], indent=2)

    return f"""## Test Case: {test_case["name"]}

**Description:** {test_case["description"]}

**User Prompt Given to Tool:** {test_case["prompt"]}

---

## Expected Findings (Ground Truth)

```json
{expected}
```

---

## Actual Output from Migration Tool

```markdown
{actual_output}
```

---

Score the actual output against the expected findings using the rubric.
Respond with ONLY valid JSON.
"""


def call_openai(system_prompt: str, user_prompt: str, model: str = "gpt-4o") -> str:
    """Call OpenAI API."""
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,  # Low temperature for consistent scoring
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


def call_bedrock(system_prompt: str, user_prompt: str, model: str = "anthropic.claude-sonnet-4-20250514-v1:0") -> str:
    """Call AWS Bedrock API."""
    import boto3

    client = boto3.client("bedrock-runtime")
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "temperature": 0.1,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_prompt}
        ],
    })
    response = client.invoke_model(
        modelId=model,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    response_body = json.loads(response["body"].read())
    return response_body["content"][0]["text"]


def evaluate_output(
    test_case: dict,
    actual_output: str,
    provider: str = "openai",
    model: Optional[str] = None,
) -> EvalResult:
    """Run LLM judge on a single test case output."""

    judge_prompt = build_judge_prompt(test_case, actual_output)

    if provider == "openai":
        raw = call_openai(JUDGE_SYSTEM_PROMPT, judge_prompt, model or "gpt-4o")
    elif provider == "bedrock":
        raw = call_bedrock(JUDGE_SYSTEM_PROMPT, judge_prompt, model or "anthropic.claude-sonnet-4-20250514-v1:0")
    else:
        raise ValueError(f"Unknown provider: {provider}")

    # Parse JSON response (handle markdown code fences if present)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:-1])
    result = json.loads(cleaned)

    scores = [
        EvalScore(
            dimension=s["dimension"],
            score=s["score"],
            reasoning=s["reasoning"],
        )
        for s in result["scores"]
    ]
    total = sum(s.score for s in scores)

    return EvalResult(
        test_case=test_case["name"],
        scores=scores,
        total_score=total,
        max_score=25,
        percentage=round((total / 25) * 100, 1),
        findings_detected=result.get("findings_detected", []),
        findings_missed=result.get("findings_missed", []),
        false_positives=result.get("false_positives", []),
        judge_summary=result.get("summary", ""),
    )


def print_result(result: EvalResult):
    """Pretty-print an evaluation result."""
    print(f"\n{'='*60}")
    print(f"  TEST CASE: {result.test_case}")
    print(f"  SCORE: {result.total_score}/{result.max_score} ({result.percentage}%)")
    print(f"{'='*60}")

    print("\n  Dimension Scores:")
    for s in result.scores:
        bar = "█" * s.score + "░" * (5 - s.score)
        print(f"    {s.dimension:<15} [{bar}] {s.score}/5  {s.reasoning}")

    if result.findings_detected:
        print(f"\n  ✅ Findings Detected ({len(result.findings_detected)}):")
        for f in result.findings_detected:
            print(f"     • {f}")

    if result.findings_missed:
        print(f"\n  ❌ Findings Missed ({len(result.findings_missed)}):")
        for f in result.findings_missed:
            print(f"     • {f}")

    if result.false_positives:
        print(f"\n  ⚠️  False Positives ({len(result.false_positives)}):")
        for f in result.false_positives:
            print(f"     • {f}")

    print(f"\n  Judge Summary: {result.judge_summary}")
    print()


def print_summary(results: list[EvalResult]):
    """Print aggregate summary across all test cases."""
    print(f"\n{'='*60}")
    print(f"  EVALUATION SUMMARY")
    print(f"{'='*60}")

    total_score = sum(r.total_score for r in results)
    max_score = sum(r.max_score for r in results)
    avg_pct = round((total_score / max_score) * 100, 1) if max_score > 0 else 0

    print(f"\n  Test Cases Evaluated: {len(results)}")
    print(f"  Overall Score: {total_score}/{max_score} ({avg_pct}%)")

    # Per-dimension averages
    dimensions = ["completeness", "accuracy", "actionability", "structure", "safety"]
    print("\n  Average by Dimension:")
    for dim in dimensions:
        dim_scores = [s.score for r in results for s in r.scores if s.dimension == dim]
        avg = round(sum(dim_scores) / len(dim_scores), 1) if dim_scores else 0
        bar = "█" * round(avg) + "░" * (5 - round(avg))
        print(f"    {dim:<15} [{bar}] {avg}/5")

    # Pass/fail threshold
    print(f"\n  {'✅ PASS' if avg_pct >= 70 else '❌ NEEDS IMPROVEMENT'} (threshold: 70%)")
    print()


def main():
    parser = argparse.ArgumentParser(description="Evaluate Graviton Migration Power outputs")
    parser.add_argument("--output", type=str, help="Path to a single migration output file to score")
    parser.add_argument("--test-case", type=str, help="Test case name (from test_cases.json)")
    parser.add_argument("--all", action="store_true", help="Score all outputs in results/ directory")
    parser.add_argument("--provider", choices=["openai", "bedrock"], default="openai", help="LLM provider")
    parser.add_argument("--model", type=str, help="Model name override")
    parser.add_argument("--results-dir", type=str, default="results", help="Directory containing outputs to evaluate")
    parser.add_argument("--save", type=str, help="Save results to JSON file")

    args = parser.parse_args()

    eval_dir = Path(__file__).parent
    test_cases_path = eval_dir / "test_cases.json"

    with open(test_cases_path) as f:
        test_cases = json.load(f)

    test_case_map = {tc["name"]: tc for tc in test_cases}

    results = []

    if args.output and args.test_case:
        # Score a single output
        if args.test_case not in test_case_map:
            print(f"Error: Unknown test case '{args.test_case}'")
            print(f"Available: {list(test_case_map.keys())}")
            sys.exit(1)

        with open(args.output) as f:
            output_text = f.read()

        result = evaluate_output(
            test_case_map[args.test_case],
            output_text,
            provider=args.provider,
            model=args.model,
        )
        results.append(result)
        print_result(result)

    elif args.all:
        # Score all outputs in results/ directory
        results_dir = eval_dir / args.results_dir
        if not results_dir.exists():
            print(f"Results directory not found: {results_dir}")
            print(f"\nTo use --all, create a '{args.results_dir}/' directory with migration outputs.")
            print(f"Name files as: <test-case-name>.md")
            print(f"\nExample:")
            print(f"  mkdir -p {results_dir}")
            print(f"  # Run the power on each sample, save output to:")
            print(f"  #   {results_dir}/graviton-migration-test.md")
            print(f"  #   {results_dir}/karpenter-test.md")
            print(f"  #   {results_dir}/pipeline-test.md")
            sys.exit(1)

        for output_file in sorted(results_dir.glob("*.md")):
            case_name = output_file.stem
            if case_name not in test_case_map:
                print(f"Warning: No test case for '{case_name}', skipping")
                continue

            print(f"Evaluating: {case_name}...")
            with open(output_file) as f:
                output_text = f.read()

            result = evaluate_output(
                test_case_map[case_name],
                output_text,
                provider=args.provider,
                model=args.model,
            )
            results.append(result)
            print_result(result)

        if results:
            print_summary(results)

    else:
        parser.print_help()
        print("\n\nQuick start:")
        print("  1. Run the power on a sample project")
        print("  2. Save the migration output to a .md file")
        print("  3. Score it:")
        print("     python eval_judge.py --output my_output.md --test-case graviton-migration-test")
        sys.exit(0)

    # Optionally save results to JSON
    if args.save and results:
        save_data = [asdict(r) for r in results]
        with open(args.save, "w") as f:
            json.dump(save_data, f, indent=2)
        print(f"Results saved to: {args.save}")


if __name__ == "__main__":
    main()
