"""代码风格筛查测试."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from code_style import analyze_code_style


def test_clean_cpp_gets_high_score():
    code = """
#include <bits/stdc++.h>
using namespace std;

int add_one(int value) {
    return value + 1;
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n;
    cin >> n;
    cout << add_one(n) << '\\n';
    return 0;
}
"""

    report = analyze_code_style(code, filename="main.cpp")

    assert report.language == "cpp"
    assert report.score >= 80
    assert report.metrics["function_count"] >= 1
    assert "结构清爽" in report.style_tags or "可维护" in report.style_tags


def test_messy_cpp_reports_style_risks():
    code = """
#include <bits/stdc++.h>
using namespace std;
#define REP(i,n) for(int i=0;i<n;i++)
#define X first
#define Y second
int a[200005],b[200005],c[200005],d[200005],e[200005],f[200005],g[200005],h[200005],ans;
int main(){int n;cin>>n;REP(i,n){cin>>a[i];if(a[i]>123456){for(int j=0;j<n;j++){if(a[j]<998244353){for(int k=0;k<n;k++){ans+=a[i]+a[j]+a[k]+7777777;}}}}}cout<<ans<<"\\n";}
"""

    report = analyze_code_style(code, filename="bad.cpp")

    assert report.language == "cpp"
    assert report.score < 90
    categories = {issue.category for issue in report.issues}
    assert {"line_length", "constants"} & categories
    assert report.metrics["magic_number_count"] > 0


def test_empty_code_is_rejected_by_analyzer():
    report = analyze_code_style("")

    assert report.score == 0
    assert report.issues[0].category == "input"
