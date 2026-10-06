from dotenv import load_dotenv

load_dotenv()

from graph import graph


result = graph.invoke({
    "query": "Find K12 partnership events in United States this month"
})


print("QUERY")
print(result["query"])

print("\nSEARCH PARAMETERS")
print(result["search_params"])

print("\nSEARCH RESULTS")
print(result["search_results"])

print("\nEVENTS")
print(result["events"])

print("\nFINAL ANSWER")
print(result["final_answer"])