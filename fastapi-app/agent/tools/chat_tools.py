from tortoise import Tortoise

from settings import TORTOISE_ORM
from utils.chat_utils import ChatAgentState
from langchain_core.messages import AIMessage, SystemMessage
from core.my_llm import MyLLM

# from langchain_openai import ChatOpenAI

llm = MyLLM

# 根据用户输入提取关键词
def understand_query_node(state: ChatAgentState) -> dict:
    """步骤1-理解用户查询并得到查询关键词"""
    user_message = state["messages"][-1].content

    understand_prompt = f"""分析用户的查询："{user_message}
请完成一个任务：
1.请从用户的查询中提取工业异常检测相关关键词，该关键词后续可用于知识库、数据集或算法信息检索

输出格式：
查询词：[最佳查询关键字]
"""
    response = llm.invoke([SystemMessage(content=understand_prompt)])
    # response = llm.invoke()
    response_text = response.content

    # 解析LLM的输出，提取搜索关键词
    search_query = user_message # 默认使用原始查询
    if "查询词：" in response_text:
        search_query = response_text.split("查询词：")[1].strip()
    # print(search_query)

    return {
        "user_query": response_text,
        "search_query": search_query,
        "step": "understood",
        "messages": [AIMessage(content=f"查询关键词为：{search_query}")]
    }

async def database_search_node(state: ChatAgentState) -> dict:
    """步骤2-占位检索节点，后续可接入数据集、算法或知识库检索"""
    search_query = state["search_query"]

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        return {
            "search_results": f"待检索关键词：{search_query}",
            "step": "searched",
            "messages": [AIMessage(content="检索节点尚未接入业务数据源，正在基于关键词整理答案...")]
        }
    except Exception as e:
        return {
            "search_results": f"搜索失败：{e}",
            "step": "search_failed",
            "messages": [AIMessage(content="搜索遇到问题...")]
        }
    finally:
        await Tortoise.close_connections()

def generate_answer_node(state: ChatAgentState) -> dict:
    """步骤3-基于搜索结果生成最终答案"""
    if state["step"] == "search_failed":
        # 如果搜索失败，执行回退策略，基于LLM自身知识回答
        fallback_prompt = f"搜索API暂时不可用，请基于您的知识回答用户的问题：\n用户问题：{state['user_query']}"
        response = llm.invoke([SystemMessage(fallback_prompt)])
    else:
        # 搜索成功，基于搜索结果生成答案
        answer_prompt = f"""基于以下搜索结果为用户提供完整、准确的答案：
用户问题：{state["user_query"]}
搜索结果：{state["search_results"]}
请综合搜索结果，提供准确、有用的回答..."""
        response = llm.invoke([SystemMessage(answer_prompt)])
    return {
        "final_answer": response.content,
        "step": "completed",
        "messages": [AIMessage(content=response.content)]
    }
