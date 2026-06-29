# Power Evaluation Framework

Automated evaluation of the Graviton Migration Power using LLM-as-a-judge.

## How It Works

1. **Test cases** define inputs (sample projects) and expected findings
2. **Run the power** against each test case and capture the migration report
3. **LLM judge** scores the output against a rubric
4. **Aggregate scores** into a summary report

## Setup

```bash
pip install openai   # or boto3 for Bedrock
```

Set your API key:
```bash
export OPENAI_API_KEY="sk-..."
# OR for Bedrock:
export AWS_PROFILE="your-profile"
```

## Running

```bash
# Score a single migration output
python eval_judge.py --output path/to/migration_output.md --test-case graviton-migration-test

# Score all test cases
python eval_judge.py --all

# Use Bedrock instead of OpenAI
python eval_judge.py --all --provider bedrock --model anthropic.claude-sonnet-4-20250514-v1:0
```

## Test Cases

Each test case in `test_cases.json` defines:
- `name`: Test case identifier
- `sample_dir`: Path to the sample project
- `prompt`: The user prompt to give the power
- `expected_findings`: What the power should identify
- `ground_truth`: Path to the known-good migration report

## Scoring Rubric

The LLM judge evaluates on 5 dimensions (1-5 scale each):

| Dimension | What It Measures |
|-----------|-----------------|
| **Completeness** | Did it find all known issues? |
| **Accuracy** | Are the findings correct (no hallucinations)? |
| **Actionability** | Are the recommendations specific and implementable? |
| **Structure** | Is the report well-organized and clear? |
| **Safety** | Does it avoid breaking changes without user consent? |

**Total score: 5-25 points**
