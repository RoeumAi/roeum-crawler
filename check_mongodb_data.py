"""
MongoDB에서 ref_no 존재 여부를 확인하는 스크립트
"""
import json
from collections import defaultdict
from scripts.core.database.mongo_client import get_mongo_db

# data_type과 MongoDB 컬렉션 매핑
COLLECTION_MAPPING = {
    'CASE': 'case',
    'ITP': 'interpretation',  # 해석례
    'DEC': 'decision',  # 심의결정례
    'ADRULE': 'adrule',
    'LAW': 'law',
    'JUDGMENT': 'judgment',
    'MEDIATION_CASE': 'mediation_case'
}

def normalize_ref_no(ref_no: str) -> str:
    """ref_no 정규화 (공백, 특수문자, 점, 대괄호 제거 후 소문자 변환)"""
    import re
    if not ref_no:
        return ""
    # 모든 공백, 점, 쉼표, 괄호, 대괄호 제거하고 소문자로
    normalized = re.sub(r'[\s\.\,\(\)\[\]]+', '', ref_no.lower())
    return normalized

def check_data_in_mongodb():
    """MongoDB에서 ref_no 존재 여부 확인"""
    
    print("=" * 80)
    print("🔍 MongoDB 데이터 존재 여부 확인 중...")
    print("=" * 80)
    
    # ref_no_data_type.json 읽기
    with open('ref_no_data_type.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # data_type별로 그룹화
    grouped = defaultdict(list)
    for item in data:
        data_type = item['data_type']
        ref_no = item['ref_no']  # 원본 저장
        grouped[data_type].append(ref_no)
    
    # MongoDB 연결
    db = get_mongo_db()
    
    results = {}
    
    # 각 data_type별로 확인
    for data_type, ref_nos in grouped.items():
        collection_name = COLLECTION_MAPPING.get(data_type)
        
        if not collection_name:
            print(f"\n⚠️  {data_type}: 매핑된 컬렉션을 찾을 수 없음")
            results[data_type] = {
                'collection': None,
                'total': len(ref_nos),
                'found': 0,
                'not_found': len(ref_nos),
                'found_list': [],
                'not_found_list': ref_nos
            }
            continue
        
        print(f"\n📂 {data_type} (컬렉션: '{collection_name}')")
        print(f"   확인할 ref_no: {len(ref_nos)}개")
        
        collection = db[collection_name]
        print(f"   MongoDB에서 기존 데이터 조회 중...")
        
        # 1. 먼저 컬렉션의 모든 ref_no를 가져오기 (훨씬 빠름)
        existing_ref_nos = set()
        original_ref_nos = {}  # 정규화된 ref_no -> 원본 매핑
        
        doc_count = 0
        for doc in collection.find({}, {'ref_no': 1, 'title': 1, 'subtitle': 1, 'doc_id': 1}):
            doc_count += 1
            if 'ref_no' in doc and doc['ref_no']:
                normalized = normalize_ref_no(doc['ref_no'])
                existing_ref_nos.add(normalized)
                original_ref_nos[normalized] = doc['ref_no']
            if 'title' in doc and doc['title']:
                normalized = normalize_ref_no(doc['title'])
                existing_ref_nos.add(normalized)
                original_ref_nos[normalized] = doc['title']
            if 'subtitle' in doc and doc['subtitle']:
                normalized = normalize_ref_no(doc['subtitle'])
                existing_ref_nos.add(normalized)
                original_ref_nos[normalized] = doc['subtitle']
        
        print(f"   컬렉션에 총 {doc_count}개 문서 발견")
        print(f"   {len(existing_ref_nos)}개의 고유 ref_no 발견")
        
        # 샘플 출력 (처음 3개)
        if len(existing_ref_nos) > 0:
            print(f"   샘플 (정규화 전): {list(original_ref_nos.values())[:3]}")
            print(f"   샘플 (정규화 후): {list(existing_ref_nos)[:3]}")
        
        print(f"   매칭 확인 중...")
        
        # 2. 메모리에서 빠르게 비교
        found_list = []
        not_found_list = []
        
        for i, ref_no in enumerate(ref_nos, 1):
            normalized = normalize_ref_no(ref_no)
            if normalized in existing_ref_nos:
                found_list.append(ref_no)
            else:
                not_found_list.append(ref_no)
            
            # 처음 몇 개는 디버깅용으로 출력
            if i <= 3:
                status = "✅" if normalized in existing_ref_nos else "❌"
                print(f"   {status} [{i}] 원본: {ref_no}")
                print(f"      정규화: {normalized}")
            
            # 진행 상황 표시 (500개마다)
            if i % 500 == 0:
                print(f"   진행: {i}/{len(ref_nos)} ({i*100//len(ref_nos)}%)")
        
        results[data_type] = {
            'collection': collection_name,
            'total': len(ref_nos),
            'found': len(found_list),
            'not_found': len(not_found_list),
            'found_list': found_list,
            'not_found_list': not_found_list
        }
        
        print(f"   ✅ 존재: {len(found_list)}개")
        print(f"   ❌ 없음: {len(not_found_list)}개")
    
    # 결과 요약
    print("\n" + "=" * 80)
    print("📊 최종 결과 요약")
    print("=" * 80)
    
    total_all = 0
    found_all = 0
    not_found_all = 0
    
    for data_type, result in results.items():
        total_all += result['total']
        found_all += result['found']
        not_found_all += result['not_found']
        
        print(f"\n{data_type}:")
        print(f"  컬렉션: {result['collection']}")
        print(f"  전체: {result['total']}개")
        print(f"  존재: {result['found']}개 ({result['found']*100//result['total'] if result['total'] > 0 else 0}%)")
        print(f"  없음: {result['not_found']}개")
    
    print("\n" + "-" * 80)
    print(f"전체 합계:")
    print(f"  전체: {total_all}개")
    print(f"  존재: {found_all}개 ({found_all*100//total_all if total_all > 0 else 0}%)")
    print(f"  없음: {not_found_all}개 ({not_found_all*100//total_all if total_all > 0 else 0}%)")
    
    # 결과를 JSON 파일로 저장
    output_file = 'mongodb_check_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 상세 결과가 '{output_file}'에 저장되었습니다.")
    
    # 없는 데이터만 별도 파일로 저장
    not_found_data = []
    for data_type, result in results.items():
        for ref_no in result['not_found_list']:
            not_found_data.append({
                'data_type': data_type,
                'ref_no': ref_no,
                'collection': result['collection']
            })
    
    not_found_file = 'ref_no_not_found.json'
    with open(not_found_file, 'w', encoding='utf-8') as f:
        json.dump(not_found_data, f, ensure_ascii=False, indent=2)
    
    print(f"💾 없는 데이터 목록이 '{not_found_file}'에 저장되었습니다.")
    
    return results

if __name__ == '__main__':
    try:
        check_data_in_mongodb()
    except Exception as e:
        print(f"\n❌ 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
