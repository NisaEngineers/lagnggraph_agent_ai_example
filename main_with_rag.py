from dotenv import load_dotenv
from typing import Annotated, Literal, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from langchain.schema import HumanMessage

load_dotenv()

# Use Ollama's llama3.2 model
llm = init_chat_model("ollama:llama3.2")

class MessageClassifier(BaseModel):
    message_type: Literal["emotional", "logical", "rag"] = Field(
        ...,
        description=(
            "Classify if the message requires an emotional (therapist), "
            "logical response, or RAG (retrieval-based answer using a PDF)."
        )
    )

class State(TypedDict):
    messages: Annotated[list, add_messages]
    message_type: Optional[str]

PDF_FILENAME = "AA.pdf"  # Path to your PDF

def classify_message(state: State):
    last_message = state["messages"][-1]
    classifier_llm = llm.with_structured_output(MessageClassifier)
    result = classifier_llm.invoke([
        {
            "role": "system",
            "content": (
                "Classify the user message as either:\n"
                "- 'emotional': if it asks for emotional support or deals with personal feelings.\n"
                "- 'logical': if it asks for facts, logical analysis, or practical solutions.\n"
                "- 'rag': if it requests information from a document (e.g., mentions 'pdf' or 'document') "
                "or requires retrieval-based generation.\n"
            )
        },
        {"role": "user", "content": last_message.content}
    ])
    return {"message_type": result.message_type}

def router(state: State):
    message_type = state.get("message_type", "logical")
    if message_type == "emotional":
        return {"next": "therapist"}
    elif message_type == "rag":
        return {"next": "rag"}
    return {"next": "logical"}

def therapist_agent(state: State):
    last_message = state["messages"][-1]
    messages = [
        {"role": "system",
         "content": (
             "You are a compassionate therapist. Focus on the emotional aspects of the user's message. "
             "Show empathy, validate their feelings, and help them process their emotions. "
             "Ask thoughtful questions to help them explore their feelings more deeply. "
             "Avoid giving logical solutions unless explicitly asked."
         )
         },
        {"role": "user", "content": last_message.content}
    ]
    reply = llm.invoke(messages)
    return {"messages": [{"role": "assistant", "content": reply.content}]}

def logical_agent(state: State):
    last_message = state["messages"][-1]
    messages = [
        {"role": "system",
         "content": (
             "You are a purely logical assistant. Focus only on facts and information. "
             "Provide clear, concise answers based on logic and evidence. "
             "Do not address emotions or provide emotional support. "
             "Be direct and straightforward in your responses."
         )
         },
        {"role": "user", "content": last_message.content}
    ]
    reply = llm.invoke(messages)
    return {"messages": [{"role": "assistant", "content": reply.content}]}

def rag_agent(state: State):
    from langchain_community.document_loaders import PyPDFLoader
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_community.vectorstores import Chroma

    last_message = state["messages"][-1]
    user_query = last_message.content

    try:
        pdf_loader = PyPDFLoader(PDF_FILENAME)
        documents = pdf_loader.load()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        docs = text_splitter.split_documents(documents)
        embeddings = HuggingFaceEmbeddings(model_name="all-mpnet-base-v2")
        vectorstore = Chroma.from_documents(docs, embeddings, collection_name="pdf_docs")
    except Exception as e:
        return {"messages": [{"role": "assistant", "content": f"RAG initialization failed: {e}"}]}

    retrieved_docs = vectorstore.similarity_search(user_query, k=3)
    context = "\n".join([doc.page_content for doc in retrieved_docs])

    prompt = (
        f"Using the context below extracted from a document:\n\n"
        f"{context}\n\n"
        f"Answer the following query: {user_query}\n\n"
        "Provide a concise and accurate answer based solely on the provided context."
    )

    messages = [
        {"role": "system", "content": "You are an assistant that uses document context to provide answers."},
        {"role": "user", "content": prompt}
    ]
    reply = llm.invoke(messages)
    return {"messages": [{"role": "assistant", "content": reply.content}]}

# Graph construction
graph_builder = StateGraph(State)
graph_builder.add_node("classifier", classify_message)
graph_builder.add_node("router", router)
graph_builder.add_node("therapist", therapist_agent)
graph_builder.add_node("logical", logical_agent)
graph_builder.add_node("rag", rag_agent)

graph_builder.add_edge(START, "classifier")
graph_builder.add_edge("classifier", "router")
graph_builder.add_conditional_edges(
    "router",
    lambda state: state.get("next"),
    {"therapist": "therapist", "logical": "logical", "rag": "rag"}
)
graph_builder.add_edge("therapist", END)
graph_builder.add_edge("logical", END)
graph_builder.add_edge("rag", END)
graph = graph_builder.compile()

def run_chatbot():
    state = {"messages": [], "message_type": None}
    while True:
        user_input = input("Message: ")
        if user_input.lower() == "exit":
            print("Bye!")
            break
        state["messages"].append(HumanMessage(content=user_input))
        state = graph.invoke(state)
        if state.get("messages"):
            print(f"Assistant: {state['messages'][-1].content}")

if __name__ == "__main__":
    run_chatbot()
