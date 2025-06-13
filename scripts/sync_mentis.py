#!/usr/bin/env python3
"""
멘티스 RSS 피드를 GitHub Project V2와 동기화하는 스크립트
"""

import os
import sys
import json
import hashlib
import feedparser
import requests
from datetime import datetime
from typing import Dict, List, Optional, Set

class GitHubProjectSync:
    def __init__(self):
        self.github_token = os.environ.get('GITHUB_TOKEN')
        self.project_id = os.environ.get('PROJECT_ID')
        self.repo_owner = os.environ.get('REPO_OWNER')
        self.repo_name = os.environ.get('REPO_NAME')
        self.mentis_rss_url = os.environ.get('MENTIS_RSS_URL')
        self.default_status = os.environ.get('PROJECT_DEFAULT_STATUS', 'Todo')  # 기본값: Todo
        
        if not all([self.github_token, self.project_id, self.repo_owner, self.repo_name, self.mentis_rss_url]):
            raise ValueError("필수 환경변수가 설정되지 않았습니다.")
        
        self.headers = {
            'Authorization': f'Bearer {self.github_token}',
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json'
        }
        
        self.graphql_headers = {
            'Authorization': f'Bearer {self.github_token}',
            'Content-Type': 'application/json'
        }
        
        # 프로젝트 필드 정보 캐시
        self.project_fields = None
        self.status_field_id = None
        self.status_options = {}

    def fetch_rss_items(self) -> List[Dict]:
        """멘티스 RSS 피드에서 아이템들을 가져옵니다."""
        try:
            print(f"RSS 피드 가져오는 중: {self.mentis_rss_url}")
            feed = feedparser.parse(self.mentis_rss_url)
            
            if feed.bozo:
                print(f"RSS 피드 파싱 경고: {feed.bozo_exception}")
            
            items = []
            for entry in feed.entries:
                item = {
                    'title': entry.get('title', '제목 없음'),
                    'url': entry.get('link', ''),
                    'description': entry.get('summary', entry.get('description', '')),
                    'published': entry.get('published', ''),
                    'guid': entry.get('id', entry.get('guid', '')),
                }
                
                # 고유 식별자 생성 (URL 기반)
                item['hash'] = hashlib.md5(item['url'].encode()).hexdigest()[:8]
                items.append(item)
            
            print(f"RSS에서 {len(items)}개 아이템 발견")
            return items
            
        except Exception as e:
            print(f"RSS 피드 가져오기 오류: {e}")
            return []

    def get_existing_issues(self) -> Dict[str, Dict]:
        """기존 GitHub 이슈들을 가져옵니다."""
        try:
            url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues"
            params = {
                'state': 'all',
                'labels': 'mentis-sync',
                'per_page': 100
            }
            
            all_issues = {}
            page = 1
            
            while True:
                params['page'] = page
                response = requests.get(url, headers=self.headers, params=params)
                response.raise_for_status()
                
                issues = response.json()
                if not issues:
                    break
                
                for issue in issues:
                    # 이슈 제목에서 해시 추출
                    title = issue['title']
                    if '[' in title and ']' in title:
                        hash_part = title.split('[')[-1].split(']')[0]
                        all_issues[hash_part] = {
                            'number': issue['number'],
                            'title': issue['title'],
                            'body': issue['body'],
                            'state': issue['state'],
                            'html_url': issue['html_url']
                        }
                
                page += 1
                if len(issues) < 100:  # 마지막 페이지
                    break
            
            print(f"기존 이슈 {len(all_issues)}개 발견")
            return all_issues
            
        except Exception as e:
            print(f"기존 이슈 가져오기 오류: {e}")
            return {}

    def create_issue(self, item: Dict) -> Optional[int]:
        """새 GitHub 이슈를 생성합니다."""
        try:
            title = f"[{item['hash']}] {item['title']}"
            body = f"""
## 멘티스 이슈

**원본 URL:** {item['url']}

**발행일:** {item['published']}

**설명:**
{item['description']}

---
*이 이슈는 멘티스 RSS에서 자동으로 동기화되었습니다.*
            """.strip()
            
            data = {
                'title': title,
                'body': body,
                'labels': ['mentis-sync']
            }
            
            url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues"
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()
            
            issue = response.json()
            print(f"새 이슈 생성: #{issue['number']} - {item['title']}")
            return issue['number']
            
        except Exception as e:
            print(f"이슈 생성 오류: {e}")
            return None

    def update_issue(self, issue_number: int, item: Dict, existing_issue: Dict) -> bool:
        """기존 GitHub 이슈를 업데이트합니다."""
        try:
            new_title = f"[{item['hash']}] {item['title']}"
            new_body = f"""
## 멘티스 이슈

**원본 URL:** {item['url']}

**발행일:** {item['published']}

**설명:**
{item['description']}

---
*이 이슈는 멘티스 RSS에서 자동으로 동기화되었습니다.*
*마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
            """.strip()
            
            # 제목이나 본문이 변경된 경우에만 업데이트
            if existing_issue['title'] != new_title or existing_issue['body'] != new_body:
                data = {
                    'title': new_title,
                    'body': new_body
                }
                
                url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues/{issue_number}"
                response = requests.patch(url, headers=self.headers, json=data)
                response.raise_for_status()
                
                print(f"이슈 업데이트: #{issue_number} - {item['title']}")
                return True
            else:
                print(f"이슈 변경사항 없음: #{issue_number}")
                return False
                
        except Exception as e:
            print(f"이슈 업데이트 오류: {e}")
            return False

    def get_project_fields(self) -> bool:
        """프로젝트의 필드 정보를 가져옵니다."""
        if self.project_fields is not None:
            return True
            
        try:
            query = """
            query($project_id: ID!) {
              node(id: $project_id) {
                ... on ProjectV2 {
                  fields(first: 20) {
                    nodes {
                      ... on ProjectV2Field {
                        id
                        name
                        dataType
                      }
                      ... on ProjectV2SingleSelectField {
                        id
                        name
                        dataType
                        options {
                          id
                          name
                        }
                      }
                    }
                  }
                }
              }
            }
            """
            
            variables = {'project_id': self.project_id}
            
            response = requests.post(
                'https://api.github.com/graphql',
                headers=self.graphql_headers,
                json={'query': query, 'variables': variables}
            )
            response.raise_for_status()
            
            result = response.json()
            if 'errors' in result:
                print(f"프로젝트 필드 조회 오류: {result['errors']}")
                return False
            
            self.project_fields = result['data']['node']['fields']['nodes']
            
            # Status 필드 찾기
            for field in self.project_fields:
                if field['name'].lower() == 'status' and 'options' in field:
                    self.status_field_id = field['id']
                    for option in field['options']:
                        self.status_options[option['name']] = option['id']
                    break
            
            if self.status_field_id:
                print(f"Status 필드 발견: {list(self.status_options.keys())}")
                if self.default_status not in self.status_options:
                    available_statuses = list(self.status_options.keys())
                    print(f"경고: 지정된 상태 '{self.default_status}'가 없습니다. 사용 가능한 상태: {available_statuses}")
                    if available_statuses:
                        self.default_status = available_statuses[0]
                        print(f"기본 상태를 '{self.default_status}'로 변경합니다.")
            else:
                print("Status 필드를 찾을 수 없습니다.")
            
            return True
            
        except Exception as e:
            print(f"프로젝트 필드 조회 오류: {e}")
            return False

    def add_issue_to_project(self, issue_number: int) -> bool:
    def add_issue_to_project(self, issue_number: int) -> bool:
        """이슈를 Project V2에 추가하고 상태를 설정합니다."""
        try:
            # 프로젝트 필드 정보가 없으면 가져오기
            if not self.get_project_fields():
                print("프로젝트 필드 정보를 가져올 수 없습니다.")
            
            # 먼저 이슈의 node_id를 가져옵니다
            url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/issues/{issue_number}"
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            issue_node_id = response.json()['node_id']
            
            # GraphQL을 사용해서 프로젝트에 아이템 추가
            add_mutation = """
            mutation($project_id: ID!, $content_id: ID!) {
              addProjectV2ItemById(input: {projectId: $project_id, contentId: $content_id}) {
                item {
                  id
                }
              }
            }
            """
            
            variables = {
                'project_id': self.project_id,
                'content_id': issue_node_id
            }
            
            response = requests.post(
                'https://api.github.com/graphql',
                headers=self.graphql_headers,
                json={'query': add_mutation, 'variables': variables}
            )
            response.raise_for_status()
            
            result = response.json()
            if 'errors' in result:
                print(f"프로젝트 추가 오류: {result['errors']}")
                return False
            
            item_id = result['data']['addProjectV2ItemById']['item']['id']
            print(f"이슈 #{issue_number}를 프로젝트에 추가")
            
            # 상태 설정 (Status 필드가 있고 기본 상태가 설정된 경우)
            if self.status_field_id and self.default_status in self.status_options:
                status_option_id = self.status_options[self.default_status]
                
                status_mutation = """
                mutation($project_id: ID!, $item_id: ID!, $field_id: ID!, $option_id: String!) {
                  updateProjectV2ItemFieldValue(input: {
                    projectId: $project_id,
                    itemId: $item_id,
                    fieldId: $field_id,
                    value: {
                      singleSelectOptionId: $option_id
                    }
                  }) {
                    projectV2Item {
                      id
                    }
                  }
                }
                """
                
                variables = {
                    'project_id': self.project_id,
                    'item_id': item_id,
                    'field_id': self.status_field_id,
                    'option_id': status_option_id
                }
                
                response = requests.post(
                    'https://api.github.com/graphql',
                    headers=self.graphql_headers,
                    json={'query': status_mutation, 'variables': variables}
                )
                response.raise_for_status()
                
                result = response.json()
                if 'errors' in result:
                    print(f"상태 설정 오류: {result['errors']}")
                else:
                    print(f"이슈 #{issue_number}의 상태를 '{self.default_status}'로 설정")
            
            return True
            
        except Exception as e:
            print(f"프로젝트 추가 오류: {e}")
            return False

    def sync(self):
        """RSS 피드와 GitHub Project를 동기화합니다."""
        print("=== 멘티스 RSS -> GitHub Project V2 동기화 시작 ===")
        
        # RSS 아이템 가져오기
        rss_items = self.fetch_rss_items()
        if not rss_items:
            print("RSS 아이템이 없습니다.")
            return
        
        # 기존 이슈들 가져오기
        existing_issues = self.get_existing_issues()
        
        # 통계
        created_count = 0
        updated_count = 0
        errors_count = 0
        
        for item in rss_items:
            item_hash = item['hash']
            
            if item_hash in existing_issues:
                # 기존 이슈 업데이트
                issue_number = existing_issues[item_hash]['number']
                if self.update_issue(issue_number, item, existing_issues[item_hash]):
                    updated_count += 1
            else:
                # 새 이슈 생성
                issue_number = self.create_issue(item)
                if issue_number:
                    # 프로젝트에 추가
                    if self.add_issue_to_project(issue_number):
                        created_count += 1
                    else:
                        errors_count += 1
                else:
                    errors_count += 1
        
        print(f"\n=== 동기화 완료 ===")
        print(f"생성된 이슈: {created_count}개")
        print(f"업데이트된 이슈: {updated_count}개")
        print(f"오류: {errors_count}개")

def main():
    try:
        syncer = GitHubProjectSync()
        syncer.sync()
    except Exception as e:
        print(f"동기화 중 오류 발생: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()