import docx2txt
import sys
text = docx2txt.process('/home/abel/Project/AMSM/AMSM指导及测试文件/AMSM_V4.0_完整需求与实施文档.docx')
with open('/home/abel/Project/AMSM/AMSM指导及测试文件/AMSM_V4.0_req.txt', 'w', encoding='utf-8') as f:
    f.write(text)
