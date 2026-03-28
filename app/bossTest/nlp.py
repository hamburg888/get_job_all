import jieba
import jieba.analyse
import re

# 1. 预处理：模拟清洗掉无意义的招聘动词
def clean_jd_text(text):
    # 去除停用动词（实际项目中应使用停用词表文件）
    stop_verbs = ["负责", "参与", "开展", "进行", "根据", "完成", "推动", "具备", "落实"]
    for word in stop_verbs:
        text = text.replace(word, "")
    return text

# 2. 定义技术词典（实际项目中词典会非常大）
tech_dict = [
    "Java", "Spring Boot", "MyBatis", "Vue", "React", "MySQL", 
    "Redis", "Kafka", "AI", "ChatGPT", "Copilot", "JVM", 
    "多线程", "全栈", "前端", "后端", "小程序"
]
# 将词典加入jieba分词器
for word in tech_dict:
    jieba.add_word(word)

# 示例文本（取自您的第3条数据片段）
jd_text = """
职位介绍
岗位职责：
作为嵌入式系统软件开发工程师，您将有机会深度参与最前沿的交互显示产品、智慧家电、汽车智能座舱或音视频产品等领域的设计与创新。通过优良的系统设计和精准的代码实现，从内核到应用全栈参与，软硬结合，为硬件赋予灵魂，为客户创造价值！
方向：单片机、Linux、ARM、Android系统
1.负责系统底层驱动的开发及调试，推进软硬件的深度协同，提升整体系统性能与稳定性；
2.参与从操作系统内核到应用层的全栈开发，推动用户交互体验的持续优化和创新升级；
3.研究行业前沿技术，将新技术应用于实际项目，提升产品智能化水平；
4.对接客户需求，从软件配置、缺陷处理、订单交付，全链条负责客户的软件交付。
岗位要求：
1.计算机、软件、电子、通信等相关专业；
2.有良好的编程基础，熟练掌握C/C++/python语言，掌握操作系统、数据结构等软件开发必备知识；
3.具有良好的团队协作意识、沟通表达能力及信息获取能力；
4.对前沿技术保持热情，对技术创新抱有热忱，善于主动学习并勇于迎接挑战；
5.能高效运用AI工具辅助开发者优先。

截止日期：2026年06月01日
招聘人数：50人
"""

# 清洗文本
cleaned_text = clean_jd_text(jd_text)

# 方法A：基于 TF-IDF 提取关键词
keywords_tfidf = jieba.analyse.extract_tags(cleaned_text, topK=10, withWeight=True)

# 方法B：自定义正则提取“熟悉/掌握”后的词（更精准）
pattern = re.compile(r'(熟悉|掌握|了解|使用)\s*([\w\u4e00-\u9fa5]+)')
keywords_regex = pattern.findall(jd_text)

print("=== 算法提取结果 ===")
print(keywords_tfidf)

print("\n=== 规则提取结果(技能点) ===")
print(keywords_regex)