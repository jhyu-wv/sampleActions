#!/usr/bin/env python3
"""
RSS to GitHub Issues Sync Script
멘티스 RSS 피드를 GitHub 칸반보드와 동기화
"""

import os
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

import feedparser
import requests
from github import Github
from dateutil import parser as date_parser

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RSSStateManager:
    """RSS 상태 관리 클래스"""
    
    def __init__(self, state_file_path: str):
        self.state_file_path = Path(state_file_path)
        self.state_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()
    
    def _load_state(self) -> Dict:
        """이전 상태 로드"""
        if self.state_file_path.exists():
            try:
                with open(self.state_file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"상태 파일 로드 실패: {e}")
        
        return {
            'last_sync': None,
            'processed_items': {},
            'github_issues': {}
        }
    
    def save_state(self):
        """현재 상태 저장"""
        try:
            with open(self.state_file_path, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
            logger.info(f"상태 저장 완료: {self.state_file_path}")
        except IOError as e:
            logger.error(f"상태 저장 실패: {e}")
    
    def get_item_hash(self, item: Dict) -> str:
        """RSS 아이템의 해시값 계산"""
        content = f"{item.get('title', '')}{item.get('link', '')}{item.get('description', '')}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    def is_item_changed(self, item_id: str, current_hash: str) -> bool:
        """아이템 변경 여부 확인"""
        stored_hash = self.state['processed_items'].get(item_id)
        return stored_hash != current_hash
    
    def update_item_state(self, item_id: str, item_hash: str, github_issue_number: int):
        """아이템 상태 업데이트"""
        self.state['processed_items'][item_id] = item_hash
        self.state['github_issues'][item_id] = github_issue_number

class GitHubIssueManager:
    """GitHub Issues 관리 클래스"""
    
    def __init__(self, github_token: str, repo_name: str):
        self.github = Github(github_token)
        self.repo = self.repo = self.github.get_repo(repo_name)
        self.github_token = github_token
        self.labels = self._ensure_labels()
        self.project_info = self._get_project_info()
    
    def _ensure_labels(self) -> Dict[str, any]:
        """필요한 라벨들이 존재하는지 확인하고 생성"""
        required_labels = {
            'mantis-rss': {'color': '599468', 'description': 'RSS 피드에서 자동 생성됨'},
            'QA': {'color': 'efddff', 'description': 'QA 이슈'}
        }
        
        existing_labels = {label.name: label for label in self.repo.get_labels()}
        labels = {}
        
        for label_name, config in required_labels.items():
            if label_name in existing_labels:
                labels[label_name] = existing_labels[label_name]
            else:
                try:
                    labels[label_name] = self.repo.create_label(
                        name=label_name,
                        color=config['color'],
                        description=config['description']
                    )
                    logger.info(f"라벨 생성: {label_name}")
                except Exception as e:
                    logger.error(f"라벨 생성 실패 {label_name}: {e}")
        
        return labels
    
    def _get_project_info(self) -> Dict:
        """프로젝트 정보 가져오기 (GitHub Projects V2 API)"""       
        try:
            # GraphQL API를 사용하여 프로젝트 정보 조회
            project_name = os.getenv('RSS_PROJECT_NAME', 'Proj')
            
            query = """
            query($owner: String!, $repo: String!) {
              repository(owner: $owner, name: $repo) {
                projectsV2(first: 10) {
                  nodes {
                    id
                    title
                    fields(first: 20) {
                      nodes {
                        ... on ProjectV2SingleSelectField {
                          id
                          name
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
            }
            """
            
            owner, repo_name = self.repo.full_name.split('/')
            variables = {"owner": owner, "repo": repo_name}
            
            response = self._execute_graphql_query(query, variables)
            
            if response and 'data' in response:
                projects = response['data']['repository']['projectsV2']['nodes']                   

                # 프로젝트 이름으로 찾기 또는 첫 번째 프로젝트 사용
                target_project = None

                for project in projects:
                    if project['title'] == project_name:
                        target_project = project
                        break
                
                if not target_project and projects:
                    target_project = projects[0]  # 첫 번째 프로젝트 사용

                if target_project:
                    # Status 필드 찾기
                    status_field = None
                    status_options = {}

                    for field in target_project['fields']['nodes']:
                        if field == {}:
                            continue

                        if field['name'].lower() in ['status', '상태']:
                            status_field = field
                            logger.warning(f" status_field ::: {status_field}")
                            logger.warning(f" field ::: {field['options']}")
                            status_options = {opt['name']: opt['id'] for opt in field['options']}
                            break
                    
                    return {
                        'project_id': target_project['id'],
                        'project_title': target_project['title'],
                        'status_field_id': status_field['id'] if status_field else None,
                        'status_options': status_options
                    }
            
            logger.warning("프로젝트 정보를 찾을 수 없습니다.")
            return {}
            
        except Exception as e:
            logger.error(f"프로젝트 정보 조회 실패: {e}")
            return {}

    def _get_milestone_id(self, ) -> Optional[str]:
        """TEST 마일스톤 ID 가져오기"""
        owner, repo_name = self.repo.full_name.split('/')
        target_milestone = os.getenv('DEFAULT_MILESTONE', 'Logcatch - QA')
        headers = {
            'Authorization': f'Bearer {self.github_token}',
            'Content-Type': 'application/vnd.github.v3+json',
        }
        url = f"https://api.github.com/repos/{owner}/{repo_name}/milestones"
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            milestones = response.json()
            for milestone in milestones:
                if milestone['title'] == target_milestone:
                    return str(milestone['number'])
            
            return None
        except Exception as e:
            print(f"마일스톤 조회 실패: {e}")
            return None

    def _get_issue_node_id(self, issue_number: int) -> Optional[str]:
        """이슈 번호로 Node ID 가져오기"""
        owner, repo_name = self.repo.full_name.split('/')
        headers = {
            'Authorization': f'Bearer {self.github_token}',
            'Content-Type': 'application/vnd.github.v3+json',
        }

        url = f"https://api.github.com/repos/{owner}/{repo_name}/issues/{issue_number}"
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            issue = response.json()
            logger.warning(f" GET issue node::: {issue}")

            return issue.get('node_id')
            
        except Exception as e:
            print(f"이슈 Node ID 조회 실패: {e}")
            return None

    def _execute_graphql_query(self, query: str, variables: Dict) -> Optional[Dict]:
        """GraphQL 쿼리 실행"""
        headers = {
            'Authorization': f'Bearer {self.github_token}',
            'Content-Type': 'application/vnd.github.v3+json',
        }
        
        data = {
            'query': query,
            'variables': variables
        }
        
        try:
            response = requests.post(
                'https://api.github.com/graphql',
                headers=headers,
                json=data,
                timeout=15
            )
            if response.status_code == 200:
                result = response.json()
                logger.info(f"GraphQL 수행 내역: {result}")

                if 'errors' in result:
                    logger.error(f"GraphQL 오류: {result['errors']}")
                    return None
                return result
            else:
                logger.error(f"GraphQL 쿼리 실패: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"GraphQL 요청 예외: {e}")
            return None
    
    def create_issue_from_rss(self, rss_item: Dict) -> Optional[int]:
        """RSS 아이템으로부터 GitHub Issue 생성"""
        try:
            title = self._clean_title(rss_item.get('title', 'Untitled'))
            body = self._generate_issue_body(rss_item)
            labels = [self.labels['mantis-rss'], self.labels['QA']]
            
            # 기본 이슈 생성
            issue = self.repo.create_issue(
                title=title,
                body=body,
                labels=labels
            )
            
            logger.info(f"이슈 생성: #{issue.number} - {title}")
            logger.info(f"생성된 이슈 상세: #{issue}") 
            logger.info(f"rss_item 상세: #{rss_item}")
            
            # 프로젝트에 이슈 추가 및 상태 설정
            if self.project_info:
                self._add_issue_to_project(issue.number, rss_item)
            
            return issue.number
            
        except Exception as e:
            logger.error(f"이슈 생성 실패: {e}")
            return None
    
    def _add_issue_to_project(self, issue_number: int, rss_item: Dict):
        """이슈를 프로젝트에 추가하고 상태 설정"""
        try:
            if not self.project_info.get('project_id'):
                logger.warning("프로젝트 ID가 없어 프로젝트 추가를 건너뜁니다.")
                return
            
            # 1. 이슈를 프로젝트에 추가
            add_mutation = """
            mutation($projectId: ID!, $contentId: ID!) {
              addProjectV2ItemByContentId(input: {
                projectId: $projectId
                contentId: $contentId
              }) {
                item {
                    id
                    content
              }
            }
            """

            logger.warning(f"프로젝트에 이슈 추가: #{issue_number}")
            
            # 이슈의 Global ID 가져오기
            issue = self.repo.get_issue(issue_number)
            logger.warning(f"  issue::: {issue}")

            project_id = self.project_info['project_id']
            logger.warning(f"  project_id::: {project_id}")

            issue_global_id = self._get_issue_node_id(issue_number)
            logger.warning(f"  issue_global_id::: {issue_global_id}")

            variables = {
                "projectId": self.project_info['project_id'],
                "contentId": issue_global_id
            }

            logger.warning(f"  variables::: {variables}")
            logger.warning(f"  add_mutation::: {add_mutation}")

            response = self._execute_graphql_query(add_mutation, variables)
            logger.warning(f"  response::: {response}")

            if response and 'data' in response:
                item_id = response['data']['addProjectV2ItemByContentId']['item']['id']
                logger.info(f"이슈 #{issue_number}를 프로젝트에 추가했습니다.")
                
                # 2. 상태 설정
                self._set_issue_status(item_id, issue_number, rss_item)
            else:
                logger.error("프로젝트에 이슈 추가 실패")

        except Exception as e:
            logger.error(f"프로젝트 추가 중 오류: {e}")
    
            
    def _set_issue_status(self, item_id: str, issue_number: int, rss_item: Dict):
        """이슈 상태 설정"""
        try:
            milestone_id = self._get_milestone_id()
            logger.warning(f"  milestone_id::: {milestone_id}")

            issue_global_id = self._get_issue_node_id(issue_number)
            logger.warning(f"  issue_global_id::: {issue_global_id}")

            if not self.project_info.get('status_field_id'):
                logger.warning("상태 필드가 없어 상태 설정을 건너뜁니다.")
                return
            
            # RSS 아이템에 따른 상태 결정 로직
            status = self._determine_status(rss_item)
            status_option_id = self.project_info['status_options'].get(status)
            
            if not status_option_id:
                logger.warning(f"상태 '{status}'를 찾을 수 없습니다.")
                return
            
            # 상태 업데이트 mutation
            update_mutation = """
            mutation($projectId: ID!, $itemId: ID!, $milestoneId: ID!, $value: ProjectV2FieldValue!) {
              updateProjectV2ItemFieldValue(input: {
                projectId: $projectId
                itemId: $itemId
                milestoneId: $milestoneId
                value: $value
              }) {
                issue {
                    id
                    milestone {
                        id
                        title
                    }
                }
              }
            }
            """
            
            variables = {
                "projectId": self.project_info['project_id'],
                "itemId": item_id,
                "milestoneId": milestone_id,
                "value": {
                    "singleSelectOptionId": status_option_id
                }
            }
            
            response = self._execute_graphql_query(update_mutation, variables)
            
            if response and 'data' in response:
                logger.info(f"이슈 상태를 '{status}'로 설정했습니다.")
            else:
                logger.error("이슈 상태 설정 실패")
                
        except Exception as e:
            logger.error(f"상태 설정 중 오류: {e}")
    
    def _determine_status(self, rss_item: Dict) -> str:
        """RSS 아이템 내용에 따른 상태 결정"""
        # 환경 변수에서 기본 상태 가져오기
        default_status = os.getenv('DEFAULT_ISSUE_STATUS', 'QA-issue')
        
        # RSS 아이템 내용 기반 상태 결정 로직
        title = rss_item.get('title', '').lower()
        description = rss_item.get('description', '').lower()
        content = f"{title} {description}"
        
        # 키워드 기반 상태 매핑
        status_keywords = {
            'In Progress': ['진행', 'working', 'developing', '개발중'],
            'Done': ['완료', 'finished', 'completed', '출시'],
            'Blocked': ['차단', 'blocked', '문제', 'issue'],
            'Review': ['리뷰', 'review', '검토', '확인']
        }
        
        for status, keywords in status_keywords.items():
            if any(keyword in content for keyword in keywords):
                # 해당 상태가 프로젝트에 존재하는지 확인
                if status in self.project_info.get('status_options', {}):
                    return status
        
        return default_status
    
    def update_issue(self, issue_number: int, rss_item: Dict):
        """기존 이슈 업데이트"""
        try:
            issue = self.repo.get_issue(issue_number)
            new_body = self._generate_issue_body(rss_item)
            
            if issue.body != new_body:
                issue.edit(body=new_body)
                logger.info(f"이슈 업데이트: #{issue_number}")
            
        except Exception as e:
            logger.error(f"이슈 업데이트 실패 #{issue_number}: {e}")
    
    def _clean_title(self, title: str) -> str:
        """제목 정리"""
        # HTML 태그 제거 및 길이 제한
        import re
        title = re.sub(r'<[^>]+>', '', title)
        return title[:100] + '...' if len(title) > 100 else title
    
    def _generate_issue_body(self, rss_item: Dict) -> str:
        """이슈 본문 생성"""
        body_parts = []
        
        # 기본 정보
        if rss_item.get('link'):
            body_parts.append(f"🔗 **원문 링크**: {rss_item['link']}")
        
        if rss_item.get('published'):
            pub_date = date_parser.parse(rss_item['published'])
            body_parts.append(f"📅 **발행일**: {pub_date.strftime('%Y-%m-%d %H:%M')}")
        
        # 설명/내용
        if rss_item.get('description'):
            body_parts.append("## 내용")
            body_parts.append(rss_item['description'])
        
        # 메타데이터
        body_parts.append("---")
        body_parts.append("*이 이슈는 RSS 피드에서 자동으로 생성되었습니다.*")
        body_parts.append(f"*마지막 동기화: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        
        return '\n\n'.join(body_parts)

class RSSGitHubSync:
    """Mantis RSS-GitHub 동기화 메인 클래스"""
    
    def __init__(self, rss_url: str, github_token: str, repo_name: str, state_file_path: str):
        self.rss_url = rss_url
        self.state_manager = RSSStateManager(state_file_path)
        self.github_manager = GitHubIssueManager(github_token, repo_name)
    
    def fetch_rss_feed(self) -> List[Dict]:
        """RSS 피드 가져오기"""
        try:
            logger.info(f"RSS 피드 요청: {self.rss_url}")
            feed = feedparser.parse(self.rss_url)
            
            if feed.bozo:
                logger.warning(f"RSS 파싱 경고: {feed.bozo_exception}")
            
            return feed.entries
            
        except Exception as e:
            logger.error(f"RSS 피드 가져오기 실패: {e}")
            return []
    
    def sync(self):
        """메인 동기화 프로세스"""
        logger.info("RSS-GitHub 동기화 시작")
        
        # RSS 피드 가져오기
        rss_items = self.fetch_rss_feed()
        if not rss_items:
            logger.warning("RSS 아이템이 없습니다.")
            return
        
        processed_count = 0
        updated_count = 0
        created_count = 0
        
        for item in rss_items:
            try:
                item_id = item.get('link') or item.get('id', '')
                if not item_id:
                    logger.warning("아이템 ID가 없어 건너뜀")
                    continue
                
                current_hash = self.state_manager.get_item_hash(item)
                
                # 변경사항 확인
                if not self.state_manager.is_item_changed(item_id, current_hash):
                    continue
                
                # GitHub Issue 처리
                existing_issue = self.state_manager.state['github_issues'].get(item_id)
                
                if existing_issue:
                    # 기존 이슈 업데이트
                    self.github_manager.update_issue(existing_issue, item)
                    updated_count += 1
                else:
                    # 새 이슈 생성
                    issue_number = self.github_manager.create_issue_from_rss(item)
                    if issue_number:
                        created_count += 1
                        existing_issue = issue_number
                
                # 상태 업데이트
                if existing_issue:
                    self.state_manager.update_item_state(item_id, current_hash, existing_issue)
                    processed_count += 1
                
            except Exception as e:
                logger.error(f"아이템 처리 중 오류: {e}")
                continue
        
        # 상태 저장 및 결과 출력
        self.state_manager.state['last_sync'] = datetime.now().isoformat()
        self.state_manager.save_state()
        
        logger.info(f"동기화 완료 - 처리: {processed_count}, 생성: {created_count}, 업데이트: {updated_count}")

def main():
    """메인 실행 함수"""
    # 환경 변수 확인
    required_env_vars = ['RSS_FEED_URL', 'GITHUB_TOKEN', 'TARGET_REPO']
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"필수 환경 변수가 누락됨: {missing_vars}")
        return 1
    
    # 동기화 실행
    sync = RSSGitHubSync(
        rss_url=os.getenv('RSS_FEED_URL'),
        github_token=os.getenv('GITHUB_TOKEN'),
        repo_name=os.getenv('TARGET_REPO'),
        state_file_path=os.getenv('STATE_FILE_PATH', 'data/rss_state.json')
    )
    
    try:
        sync.sync()
        return 0
    except Exception as e:
        logger.error(f"동기화 실패: {e}")
        return 1

if __name__ == '__main__':
    exit(main())
