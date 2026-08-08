# Practice Branch — Team Training

Yeh branch sirf **practice/sikhne** ke liye hai. Yahan kuch bhi kharab nahi hoga — yeh `main` se bilkul alag hai.

## Kya karein yahan

Naye developers yahan Git commands practice kar sakte hain bina darr ke:

```bash
cd ~/TradePro
git checkout practice/team-training
git pull origin practice/team-training

# Apna khud ka test branch banao
git checkout -b practice/<apna-naam>-test

# Koi bhi file mein chhota change karo, is file mein apna naam add karo neeche
# Phir commit + push karo
git add .
git commit -m "Practice commit by <apna naam>"
git push origin practice/<apna-naam>-test

# GitHub pe jaake dekho apna branch — Pull Request banane ki practice bhi karo
# (base branch practice/team-training rakhna, main NAHI)
```

## Practice Checklist

- [ ] `git checkout` se branch switch karna
- [ ] `git pull` se latest code lena
- [ ] Naya branch banana (`git checkout -b`)
- [ ] File edit karke commit karna
- [ ] `git push` se GitHub pe bhejna
- [ ] GitHub pe Pull Request banana
- [ ] Apna PR merge karna (isi practice branch mein, main mein nahi)

## Neeche apna naam likh sakte hain (practice ke liye)

- (yahan apna naam aur date add karo jab practice karo)

---

**Yaad rakhein:** Is branch se kabhi bhi `main` mein PR mat banao. Yeh sirf practice ke liye hai.
