"""Demo: How the agent discovers and calls tools"""
import json
import tempfile
import os

from tools import get_all_tools, get_tool_by_name


def demo_discover_tools():
    """Demo: Agent discovers all available tools."""
    print("\n" + "="*70)
    print("DEMO 1: Agent Discovers Available Tools")
    print("="*70)

    tools = get_all_tools()
    print(f"\nAvailable tools ({len(tools)}):")
    for i, tool in enumerate(tools, 1):
        print(f"  {i}. {tool.name:<30} {tool.description[:40]}...")


def demo_inspect_tool():
    """Demo: Agent inspects a tool's interface."""
    print("\n" + "="*70)
    print("DEMO 2: Agent Inspects Tool Interface")
    print("="*70)

    tool = get_tool_by_name("fetch_and_sample")
    print(f"\nTool: {tool.name}")
    print(f"Description: {tool.description}")

    print(f"\nInput Schema:")
    print(json.dumps(tool.input_schema, indent=2)[:300] + "...")

    print(f"\nOutput Schema:")
    print(json.dumps(tool.output_schema, indent=2)[:300] + "...")


def demo_call_tool():
    """Demo: Agent calls a tool."""
    print("\n" + "="*70)
    print("DEMO 3: Agent Calls Tool")
    print("="*70)

    # Create test file
    csv_content = "id,name,email\n1,Alice,alice@test.com\n2,Bob,bob@test.com\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        temp_path = f.name

    try:
        tool = get_tool_by_name("fetch_and_sample")

        print(f"\nAgent prepares input:")
        input_data = {
            "source_path": temp_path,
            "sample_size": 5,
        }
        print(json.dumps(input_data, indent=2))

        print(f"\nAgent calls tool...")
        response_json = tool(input_data)
        response = json.loads(response_json)

        print(f"\nTool response:")
        print(f"  Status: {response['status']}")
        print(f"  Source: {response['source_type']}")
        print(f"  Format: {response['detected_format_hint']}")
        print(f"  Header: {response['first_line_is_header']}")
        print(f"  Rows: {response['sample_size']}")

        print(f"\nRaw data sample:")
        print(response["raw_sample"])

    finally:
        os.unlink(temp_path)


def demo_agent_pipeline():
    """Demo: Complete agent pipeline using multiple tools."""
    print("\n" + "="*70)
    print("DEMO 4: Agent Pipeline (Tool Chaining)")
    print("="*70)

    # Create test file
    csv_content = "id,name,salary\n1,Alice,85000\n2,Bob,92000\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        temp_path = f.name

    try:
        print(f"\nAgent Pipeline:")
        print(f"  Step 1: Fetch data")
        print(f"  Step 2: Infer schema (not implemented)")
        print(f"  Step 3: Generate parser (not implemented)")
        print(f"  Step 4: Test parser (not implemented)")
        print(f"  Step 5: Load to BQ (not implemented)")

        # Step 1: Fetch data
        print(f"\n--- STEP 1: Fetch Data ---")
        tool1 = get_tool_by_name("fetch_and_sample")
        result1 = json.loads(tool1({"source_path": temp_path}))

        if result1["status"] == "success":
            print(f"✓ Fetched {result1['sample_size']} rows")
            print(f"  Format: {result1['detected_format_hint']}")
            raw_sample = result1["raw_sample"]

            # Step 2+: Other tools would be called here
            print(f"\n--- STEP 2: Infer Schema ---")
            print(f"  (Tool 2 not implemented yet)")
            print(f"  Would analyze: {len(raw_sample)} bytes of data")

            print(f"\n--- STEP 3: Generate Parser ---")
            print(f"  (Tool 3 not implemented yet)")

            print(f"\n--- STEP 4: Test Parser ---")
            print(f"  (Tool 4 not implemented yet)")

            print(f"\n--- STEP 5: Load to BigQuery ---")
            print(f"  (Tool 5 not implemented yet)")

            print(f"\n✓ Agent pipeline ready to execute!")

    finally:
        os.unlink(temp_path)


def demo_error_handling():
    """Demo: Agent handles tool errors gracefully."""
    print("\n" + "="*70)
    print("DEMO 5: Agent Error Handling")
    print("="*70)

    tool = get_tool_by_name("fetch_and_sample")

    print(f"\nAgent tries to fetch from nonexistent file:")
    result = json.loads(tool({"source_path": "/nonexistent/file.csv"}))

    print(f"  Status: {result['status']}")
    print(f"  Error: {result['error']}")

    print(f"\nAgent handles the error gracefully:")
    if result["status"] == "error":
        print(f"  -> Logs error: {result['error']}")
        print(f"  -> Tries alternative source")
        print(f"  -> Or notifies user")


def main():
    """Run all demos."""
    print("\n" + "🤖 "*35)
    print("AGENT TOOL CALLING DEMO")
    print("🤖 "*35)

    demo_discover_tools()
    demo_inspect_tool()
    demo_call_tool()
    demo_agent_pipeline()
    demo_error_handling()

    print("\n" + "="*70)
    print("✅ TOOLS READY FOR AGENT")
    print("="*70)
    print("\nAgent can now:")
    print("  • Discover all available tools via get_all_tools()")
    print("  • Get tool by name via get_tool_by_name(name)")
    print("  • Inspect tool interface (input/output schema)")
    print("  • Call tools with JSON inputs")
    print("  • Handle errors gracefully")
    print("  • Chain tools together in a pipeline")
    print("\nNext: Implement remaining tools and agent loop")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
